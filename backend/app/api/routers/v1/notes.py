"""
Notes API router.

REST:
  GET    /notes                               list notes
  POST   /notes                               create note
  GET    /notes/{note_id}                     get note
  PUT    /notes/{note_id}                     update editor content / title
  DELETE /notes/{note_id}                     delete note
  POST   /notes/{note_id}/transcript/flag     flag/unflag a transcript line
  GET    /notes/{note_id}/summary/topics-suggest  LLM-suggested topics
  POST   /notes/{note_id}/summary/speakers    save speaker mappings (Step 0)
  POST   /notes/{note_id}/summary/extract     run topic extraction (Step 2)
  POST   /notes/{note_id}/summary/delta/{delta_id}  approve/edit/dismiss delta (Step 3)

WebSocket:
  WS  /notes/ws/recording/{note_id}?mode=wasapi|browser&language=en-US
      - mode=wasapi:  server captures WASAPI loopback, streams transcript
      - mode=browser: client sends raw audio bytes, server forwards to Deepgram
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_llm_provider, get_db_repo
from backend.app.db.session import get_db_session
from backend.app.models.api_contracts import APIResponse
import json

from backend.app.services.notes_service import NotesService
from backend.app.services.meeting_summary_service import MeetingSummaryService

router = APIRouter()

RECORDINGS_DIR = Path(__file__).resolve().parents[5] / "data" / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

TENANT_ID = "Institutional_L1"   # TODO: replace with real auth


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateNoteRequest(BaseModel):
    title: str
    note_type: str
    company_tickers: List[str]
    meeting_date: Optional[str] = None
    ux_variant: str = "A"


class UpdateNoteRequest(BaseModel):
    title: Optional[str] = None
    editor_content: Optional[dict] = None
    editor_plain_text: Optional[str] = None
    company_tickers: Optional[List[str]] = None
    meeting_date: Optional[str] = None
    recording_path: Optional[str] = None


class FlagLineRequest(BaseModel):
    line_id: int
    flagged: bool


class SaveTranscriptRequest(BaseModel):
    transcript_lines: List[dict]
    duration_seconds: int


class SaveSpeakersRequest(BaseModel):
    mappings: List[dict]  # [{"label": "Speaker 0", "name": "John", "role": "CFO"}]


class ExtractTopicsRequest(BaseModel):
    topics: List[str]


class DeltaActionRequest(BaseModel):
    action: str           # "approve" | "edit" | "dismiss"
    edited_text: Optional[str] = None


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=APIResponse)
def list_notes(
    ticker: Optional[str] = None,
    note_type: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db_session),
):
    svc = NotesService(db)
    notes = svc.list_notes(TENANT_ID, ticker=ticker, note_type=note_type, limit=limit)
    return APIResponse(
        success=True,
        data=[n.model_dump() for n in notes],
        metadata={"count": len(notes)},
    )


@router.post("", response_model=APIResponse)
def create_note(request: CreateNoteRequest, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    note = svc.create_note(
        tenant_id=TENANT_ID,
        title=request.title,
        note_type=request.note_type,
        company_tickers=request.company_tickers,
        meeting_date=request.meeting_date,
        ux_variant=request.ux_variant,
    )
    return APIResponse(success=True, data=note.model_dump())


@router.get("/{note_id}", response_model=APIResponse)
def get_note(note_id: str, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    note = svc.get_note(note_id, TENANT_ID)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data=note.model_dump())


@router.put("/{note_id}", response_model=APIResponse)
def update_note(note_id: str, request: UpdateNoteRequest, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    kwargs = {}
    if request.title is not None:
        kwargs["title"] = request.title
    if request.editor_content is not None:
        kwargs["editor_content"] = request.editor_content
    if request.editor_plain_text is not None:
        kwargs["editor_plain_text"] = request.editor_plain_text
    if request.company_tickers is not None:
        kwargs["company_tickers"] = request.company_tickers
    if request.meeting_date is not None:
        kwargs["meeting_date"] = request.meeting_date
    if request.recording_path is not None:
        kwargs["recording_path"] = request.recording_path
    updated = svc.update_note(note_id, TENANT_ID, **kwargs)
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data=updated.model_dump())


@router.delete("/{note_id}", response_model=APIResponse)
def delete_note(note_id: str, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    deleted = svc.delete_note(note_id, TENANT_ID)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data={"deleted": note_id})


@router.get("/audio/{filename}")
def serve_audio(filename: str):
    """Serve audio files from the recordings directory."""
    audio_dir = Path(__file__).resolve().parents[5] / "tools" / "audio_recorder" / "recordings"
    filepath = audio_dir / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")

    media_types = {
        ".opus": "audio/ogg; codecs=opus",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }
    ext = filepath.suffix.lower()
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        filepath,
        media_type=media_type,
        filename=filename,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.post("/{note_id}/transcript/flag", response_model=APIResponse)
def flag_transcript_line(note_id: str, request: FlagLineRequest, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    updated = svc.flag_transcript_line(note_id, TENANT_ID, request.line_id, request.flagged)
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data={"line_id": request.line_id, "flagged": request.flagged})


@router.post("/{note_id}/transcript", response_model=APIResponse)
def save_transcript(note_id: str, request: SaveTranscriptRequest, db: Session = Depends(get_db_session)):
    """
    Persist the raw live-transcript lines for this note.
    Called by the frontend when recording stops so downstream wizard / AI analysis
    can read transcript_lines from the DB instead of relying on client-only state.
    """
    svc = NotesService(db)
    updated = svc.save_transcript(
        note_id=note_id,
        tenant_id=TENANT_ID,
        transcript_lines=request.transcript_lines,
        duration_seconds=request.duration_seconds,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data=updated.model_dump())


# ---------------------------------------------------------------------------
# Summary / Wizard endpoints
# ---------------------------------------------------------------------------

@router.get("/{note_id}/summary/topics-suggest", response_model=APIResponse)
def suggest_topics(
    note_id: str,
    db: Session = Depends(get_db_session),
    llm=Depends(get_llm_provider),
    db_repo=Depends(get_db_repo),
):
    svc = MeetingSummaryService(db, db_repo, llm)
    suggestions = svc.suggest_topics(note_id, TENANT_ID)
    return APIResponse(success=True, data={"suggestions": suggestions})


@router.post("/{note_id}/summary/speakers", response_model=APIResponse)
def save_speakers(
    note_id: str,
    request: SaveSpeakersRequest,
    db: Session = Depends(get_db_session),
    llm=Depends(get_llm_provider),
    db_repo=Depends(get_db_repo),
):
    svc = MeetingSummaryService(db, db_repo, llm)
    try:
        note = svc.save_speaker_mappings(note_id, TENANT_ID, request.mappings)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return APIResponse(success=True, data=note.model_dump())


@router.post("/{note_id}/summary/extract", response_model=APIResponse)
def extract_topics(
    note_id: str,
    request: ExtractTopicsRequest,
    db: Session = Depends(get_db_session),
    llm=Depends(get_llm_provider),
    db_repo=Depends(get_db_repo),
):
    # Empty topics list is allowed — the service derives topics from the user's
    # own notes + transcript in that case.
    svc = MeetingSummaryService(db, db_repo, llm)
    try:
        note = svc.extract_topic_fragments(note_id, TENANT_ID, request.topics)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return APIResponse(success=True, data=note.model_dump())


@router.post("/{note_id}/summary/complete", response_model=APIResponse)
def mark_summary_complete(
    note_id: str,
    db: Session = Depends(get_db_session),
    llm=Depends(get_llm_provider),
    db_repo=Depends(get_db_repo),
):
    """Flip summary_status to COMPLETE. Used to unstick notes left in the
    legacy AWAITING_APPROVAL state by the pre-deprecation delta flow."""
    svc = MeetingSummaryService(db, db_repo, llm)
    note = svc.mark_complete(note_id, TENANT_ID)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data=note.model_dump())


@router.post("/{note_id}/summary/regenerate", response_model=APIResponse)
def regenerate_summary(
    note_id: str,
    db: Session = Depends(get_db_session),
):
    """Re-run the structured AI summary against the note's existing transcript
    segments. NO audio cost — this is a text-only Gemini call (~$0.001-0.01).
    Overwrites polished_transcript_meta.summary with the fresh result.

    Callers: the frontend's [Re-generate Summary] button + future chat-agent
    tool. Useful after tweaking the summary prompt, or to promote legacy
    string-shaped all_numbers entries to the new NumberMention schema."""
    from backend.app.services.live_transcription import gemini_generate_summary

    svc = NotesService(db)
    note = svc.get_note(note_id, TENANT_ID)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")

    meta = note.polished_transcript_meta or {}
    segments = meta.get("segments") or []
    if not segments:
        raise HTTPException(
            status_code=400,
            detail="Note has no transcript segments to summarise. Run a recording or URL ingest first.",
        )

    language = note.polished_transcript_language or meta.get("language") or "en"
    summary_result = gemini_generate_summary(
        segments=segments,
        language_hint=language,
        note_id=note_id,
    )

    if summary_result.get("error"):
        raise HTTPException(status_code=502, detail=summary_result["error"])

    # Merge the new summary + updated token usage into the existing meta dict.
    new_meta = {
        **meta,
        "summary": summary_result["summary"],
        "summary_regenerated_at": datetime.utcnow().isoformat(),
        "summary_input_tokens": summary_result.get("input_tokens", 0),
        "summary_output_tokens": summary_result.get("output_tokens", 0),
    }
    updated = svc.save_polished_transcript(
        note_id=note_id,
        tenant_id=TENANT_ID,
        markdown=note.polished_transcript or "",
        language=language,
        meta=new_meta,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to persist regenerated summary.")

    return APIResponse(success=True, data=updated.model_dump())


@router.post("/{note_id}/summary/delta/{delta_id}", response_model=APIResponse)
def process_delta(
    note_id: str,
    delta_id: str,
    request: DeltaActionRequest,
    db: Session = Depends(get_db_session),
    llm=Depends(get_llm_provider),
    db_repo=Depends(get_db_repo),
):
    if request.action not in ("approve", "edit", "dismiss"):
        raise HTTPException(status_code=400, detail="action must be 'approve', 'edit', or 'dismiss'.")
    svc = MeetingSummaryService(db, db_repo, llm)
    try:
        note = svc.process_delta(note_id, TENANT_ID, delta_id, request.action, request.edited_text)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return APIResponse(success=True, data=note.model_dump())


# ---------------------------------------------------------------------------
# WebSocket — Live Recording
# ---------------------------------------------------------------------------

@router.websocket("/ws/recording/{note_id}")
async def recording_websocket(
    websocket: WebSocket,
    note_id: str,
    mode: str = Query(default="wasapi"),
    language: str = Query(default="en-US"),
    audio_source: str = Query(default="mic"),
):
    """
    Bidirectional WebSocket for live meeting transcription.

    mode=wasapi:
      Server captures system audio via WASAPI loopback and streams to Deepgram.
      Frontend only receives; it never sends audio.
      Messages received by frontend: JSON {"type": "transcript", "line_id": n,
        "timestamp": "HH:MM:SS", "speaker_label": "Speaker 0", "text": "...", "is_interim": true/false}

    mode=browser:
      Frontend sends raw PCM audio bytes (16kHz mono int16).
      Server forwards to Deepgram and sends transcript lines back.
      Use this when backend is remote or for microphone-only capture.

    In both modes the frontend can also send:
      {"type": "flag", "line_id": N}  ->  server marks that line as flagged
      {"type": "stop"}                ->  server stops recording gracefully
    """
    await websocket.accept()

    # live_v2 mode uses SenseVoice + Gemini — no Deepgram needed
    if mode == "live_v2":
        await _run_live_v2_session(websocket, note_id, language, audio_source)
        return

    # Legacy modes require Deepgram
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    if not api_key:
        await websocket.send_json({"type": "error", "message": "DEEPGRAM_API_KEY not configured."})
        await websocket.close()
        return

    if mode == "wasapi":
        await _run_wasapi_session(websocket, note_id, language, api_key)
    else:
        await _run_browser_session(websocket, note_id, language, api_key)


# ---------------------------------------------------------------------------
# WASAPI loopback recording session
# ---------------------------------------------------------------------------

async def _run_wasapi_session(
    websocket: WebSocket,
    note_id: str,
    language: str,
    api_key: str,
):
    """
    Adapts the standalone LiveTranscriber to a FastAPI WebSocket.
    Captures WASAPI loopback in a background thread; sends transcript
    lines to the frontend as JSON messages.
    """
    transcript_queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    line_counter = [0]
    loop = asyncio.get_event_loop()

    def transcription_thread():
        try:
            import sounddevice as sd
            import numpy as np
            from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
            import datetime as dt
            import queue as q

            audio_q: "q.Queue[bytes]" = q.Queue()
            deepgram = DeepgramClient(api_key)
            dg_conn = deepgram.listen.live.v("1")

            def on_message(self_ref, result, **kwargs):
                alt = result.channel.alternatives[0]
                text = alt.transcript.strip()
                if not text:
                    return
                is_interim = not result.is_final
                # Deepgram speaker diarization
                speaker_label = "Speaker 0"
                if alt.words:
                    spk = getattr(alt.words[0], "speaker", None)
                    if spk is not None:
                        speaker_label = f"Speaker {spk}"
                ts = dt.datetime.now().strftime("%H:%M:%S")
                line_counter[0] += 1
                msg = {
                    "type": "transcript",
                    "line_id": line_counter[0],
                    "timestamp": ts,
                    "speaker_label": speaker_label,
                    "text": text,
                    "is_interim": is_interim,
                }
                loop.call_soon_threadsafe(transcript_queue.put_nowait, msg)

            dg_conn.on(LiveTranscriptionEvents.Transcript, on_message)

            opts = LiveOptions(
                model="nova-2",
                language=language,
                smart_format=True,
                interim_results=True,
                utterance_end_ms="1000",
                diarize=True,
                encoding="linear16",
                sample_rate=16000,
                channels=1,
            )
            if not dg_conn.start(opts):
                loop.call_soon_threadsafe(
                    transcript_queue.put_nowait,
                    {"type": "error", "message": "Failed to connect to Deepgram"},
                )
                return

            # Find loopback device
            device_idx = None
            for i, dev in enumerate(sd.query_devices()):
                name = dev["name"].lower()
                if ("loopback" in name or "stereo mix" in name) and dev["max_input_channels"] > 0:
                    device_idx = i
                    break

            def audio_callback(indata, frames, time, status):
                if stop_event.is_set():
                    raise sd.CallbackStop()
                mono = np.mean(indata, axis=1) if indata.shape[1] > 1 else indata[:, 0]
                audio_q.put_nowait((mono * 32767).astype(np.int16).tobytes())

            def sender():
                while not stop_event.is_set():
                    try:
                        data = audio_q.get(timeout=0.1)
                        dg_conn.send(data)
                    except q.Empty:
                        pass

            sender_t = threading.Thread(target=sender, daemon=True)
            sender_t.start()

            with sd.InputStream(
                device=device_idx,
                samplerate=16000,
                channels=min(2, sd.query_devices(device_idx or sd.default.device[0])["max_input_channels"]),
                callback=audio_callback,
                blocksize=1600,
            ):
                while not stop_event.is_set():
                    import time
                    time.sleep(0.05)

            dg_conn.finish()

        except Exception as exc:
            loop.call_soon_threadsafe(
                transcript_queue.put_nowait, {"type": "error", "message": str(exc)}
            )

    t = threading.Thread(target=transcription_thread, daemon=True)
    t.start()

    flagged_lines: set = set()

    try:
        while True:
            # Drain transcript queue
            while not transcript_queue.empty():
                msg = transcript_queue.get_nowait()
                await websocket.send_json(msg)

            # Check for client messages (non-blocking)
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.05)
                if data.get("type") == "stop":
                    break
                if data.get("type") == "flag":
                    flagged_lines.add(data.get("line_id"))
                    await websocket.send_json({"type": "flagged", "line_id": data["line_id"]})
            except asyncio.TimeoutError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        await websocket.send_json({"type": "stopped", "note_id": note_id})


# ---------------------------------------------------------------------------
# Browser microphone session (client sends audio bytes)
# ---------------------------------------------------------------------------

async def _run_browser_session(
    websocket: WebSocket,
    note_id: str,
    language: str,
    api_key: str,
):
    """
    Client sends raw PCM audio bytes (16kHz mono int16) as binary WebSocket frames.
    Server forwards to Deepgram and echoes transcript lines back as JSON.
    """
    transcript_queue: asyncio.Queue = asyncio.Queue()
    deepgram_send_queue: asyncio.Queue = asyncio.Queue()
    line_counter = [0]
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def deepgram_thread():
        try:
            from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
            import queue as q
            import datetime as dt

            audio_q: "q.Queue" = q.Queue()
            deepgram = DeepgramClient(api_key)
            dg_conn = deepgram.listen.live.v("1")

            def on_message(self_ref, result, **kwargs):
                alt = result.channel.alternatives[0]
                text = alt.transcript.strip()
                if not text:
                    return
                is_interim = not result.is_final
                speaker_label = "Speaker 0"
                if alt.words:
                    spk = getattr(alt.words[0], "speaker", None)
                    if spk is not None:
                        speaker_label = f"Speaker {spk}"
                ts = dt.datetime.now().strftime("%H:%M:%S")
                line_counter[0] += 1
                msg = {
                    "type": "transcript",
                    "line_id": line_counter[0],
                    "timestamp": ts,
                    "speaker_label": speaker_label,
                    "text": text,
                    "is_interim": is_interim,
                }
                loop.call_soon_threadsafe(transcript_queue.put_nowait, msg)

            dg_conn.on(LiveTranscriptionEvents.Transcript, on_message)
            opts = LiveOptions(
                model="nova-2",
                language=language,
                smart_format=True,
                interim_results=True,
                diarize=True,
                encoding="linear16",
                sample_rate=16000,
                channels=1,
            )
            if not dg_conn.start(opts):
                loop.call_soon_threadsafe(
                    transcript_queue.put_nowait,
                    {"type": "error", "message": "Deepgram connection failed"},
                )
                return

            while True:
                try:
                    chunk = audio_q.get(timeout=1.0)
                    if chunk is None:
                        break
                    dg_conn.send(chunk)
                except q.Empty:
                    pass

            dg_conn.finish()

        except Exception as exc:
            loop.call_soon_threadsafe(
                transcript_queue.put_nowait, {"type": "error", "message": str(exc)}
            )

    # Bridge: move audio from asyncio queue to thread queue
    import queue as sync_q
    audio_thread_q: sync_q.Queue = sync_q.Queue()

    def bridge():
        while True:
            try:
                chunk = audio_thread_q.get(timeout=1.0)
                if chunk is None:
                    break
            except sync_q.Empty:
                if stop_event.is_set():
                    break

    t = threading.Thread(target=deepgram_thread, daemon=True)
    t.start()

    try:
        while True:
            # Flush transcript messages
            while not transcript_queue.empty():
                await websocket.send_json(transcript_queue.get_nowait())

            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

            if "bytes" in msg:
                # Audio chunk from browser MediaRecorder
                audio_thread_q.put_nowait(msg["bytes"])
            elif "text" in msg:
                import json
                try:
                    data = json.loads(msg["text"])
                except Exception:
                    continue
                if data.get("type") == "stop":
                    break
                if data.get("type") == "flag":
                    await websocket.send_json({"type": "flagged", "line_id": data.get("line_id")})

    except WebSocketDisconnect:
        pass
    finally:
        audio_thread_q.put_nowait(None)
        stop_event.set()
        await websocket.send_json({"type": "stopped", "note_id": note_id})


# ---------------------------------------------------------------------------
# Live V2 — Language-aware SenseVoice + Gemini batch polish
# ---------------------------------------------------------------------------

async def _run_live_v2_session(
    websocket: WebSocket,
    note_id: str,
    language: str,
    audio_source: str = "mic",
):
    """
    Option B: SenseVoice live draft + Gemini batch polish.
    audio_source: "mic" = browser sends PCM, "system" = server captures WASAPI loopback
    """
    from backend.app.services.live_transcription import gemini_batch_transcribe, gemini_generate_summary
    from backend.app.services.asr_worker import transcribe_audio_bytes, is_model_ready, is_model_loading
    import numpy as np
    import scipy.io.wavfile as wavfile
    import logging

    logger = logging.getLogger("live_v2")

    detected_lang = language if language in ("zh", "ja", "ko", "en") else "auto"
    language_detected = False
    line_counter = 0

    # Stream audio to disk instead of RAM to prevent memory issues
    audio_dir = Path(__file__).resolve().parents[5] / "tools" / "audio_recorder" / "recordings"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = str(audio_dir / f"{note_id}.wav")

    # Write WAV header, then append raw PCM chunks
    import wave
    wav_file = wave.open(wav_path, "wb")
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)  # 16-bit
    wav_file.setframerate(16000)

    # Keep only last 15s in RAM for live transcription
    recent_audio = bytearray()
    total_bytes = [0]  # mutable list so WASAPI thread can update it

    from backend.app.services.asr_worker import transcribe_audio_bytes, is_model_ready, is_model_loading

    stop_event = threading.Event()
    wasapi_thread = None  # will be set if audio_source == "system"

    if is_model_ready():
        await websocket.send_json({"type": "status", "status": "ready",
                                    "message": f"SenseVoice ready. Source: {audio_source}. Live transcript + Gemini polish."})
    elif is_model_loading():
        await websocket.send_json({"type": "status", "status": "starting",
                                    "message": "SenseVoice loading (~30s)... Recording starts immediately."})
    else:
        await websocket.send_json({"type": "status", "status": "starting",
                                    "message": "Loading ASR model... Recording starts immediately."})

    # For system audio: capture WASAPI loopback in a background thread
    if audio_source == "system":
        def wasapi_capture_thread():
            try:
                import sounddevice as sd
                import queue as q
                from scipy.signal import resample_poly

                audio_q: "q.Queue[np.ndarray]" = q.Queue()

                # Find loopback device
                device_idx = None
                for i, dev in enumerate(sd.query_devices()):
                    name = dev["name"].lower()
                    if ("loopback" in name or "stereo mix" in name) and dev["max_input_channels"] > 0:
                        device_idx = i
                        break

                if device_idx is None:
                    logger.error("No loopback device found")
                    return

                dev_info = sd.query_devices(device_idx)
                native_rate = int(dev_info["default_samplerate"])
                channels = min(2, int(dev_info["max_input_channels"]))
                logger.info(f"WASAPI: device={dev_info['name']}, rate={native_rate}, ch={channels}")

                def audio_cb(indata, frames, time_info, status):
                    if stop_event.is_set():
                        raise sd.CallbackStop()
                    audio_q.put_nowait(indata.copy())

                with sd.InputStream(device=device_idx, samplerate=native_rate, channels=channels,
                                     callback=audio_cb, blocksize=native_rate // 10, dtype="float32"):
                    while not stop_event.is_set():
                        try:
                            chunk = audio_q.get(timeout=0.2)
                            # Convert to mono
                            mono = np.mean(chunk, axis=1) if chunk.shape[1] > 1 else chunk[:, 0]
                            # Resample to 16kHz if needed
                            if native_rate != 16000:
                                # Simple decimation for common rates
                                if native_rate == 48000:
                                    mono = mono[::3]  # 48000/3 = 16000
                                elif native_rate == 44100:
                                    mono = resample_poly(mono, 16000, 44100)
                                else:
                                    ratio = 16000 / native_rate
                                    new_len = int(len(mono) * ratio)
                                    indices = np.linspace(0, len(mono) - 1, new_len).astype(int)
                                    mono = mono[indices]

                            pcm = (mono * 32767).astype(np.int16).tobytes()
                            wav_file.writeframes(pcm)
                            total_bytes[0] += len(pcm)
                            recent_audio.extend(pcm)
                            if len(recent_audio) > 480000:
                                del recent_audio[:len(recent_audio) - 480000]
                        except q.Empty:
                            pass
            except Exception as e:
                logger.error(f"WASAPI capture error: {e}", exc_info=True)

        wasapi_thread = threading.Thread(target=wasapi_capture_thread, daemon=True, name="wasapi-capture")
        wasapi_thread.start()

    try:
        last_transcribe_bytes = 0

        while True:
            try:
                # Short timeout for system audio (so we can check buffer periodically)
                timeout = 2 if audio_source == "system" else 300
                data = await asyncio.wait_for(websocket.receive(), timeout=timeout)
            except asyncio.TimeoutError:
                if wasapi_thread is not None:
                    # Check if we have enough audio to transcribe
                    if total_bytes[0] - last_transcribe_bytes >= 256000 and is_model_ready():
                        chunk_bytes = bytes(recent_audio)
                        audio_len_s = total_bytes[0] / (16000 * 2)
                        try:
                            result = await asyncio.to_thread(transcribe_audio_bytes, chunk_bytes)
                            text = result.get("text", "")
                            lang = result.get("language", "")

                            if text:
                                line_counter += 1
                                mins = int(audio_len_s // 60)
                                secs = int(audio_len_s % 60)

                                # Translate to English if non-English
                                translation = ""
                                if lang in ("zh", "ja", "ko") and text:
                                    try:
                                        import requests as req
                                        from dotenv import load_dotenv
                                        load_dotenv(Path(__file__).resolve().parents[5] / ".env")
                                        gkey = os.environ.get("GEMINI_API_KEY", "")
                                        if gkey:
                                            tr_resp = req.post(
                                                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gkey}",
                                                json={"contents": [{"parts": [{"text": f"Translate to English (financial meeting context). Return ONLY the translation.\n\n{text}"}]}],
                                                      "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}},
                                                timeout=10,
                                            )
                                            if tr_resp.status_code == 200:
                                                translation = tr_resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                                    except Exception:
                                        pass

                                await websocket.send_json({
                                    "type": "transcript", "line_id": line_counter,
                                    "timestamp": f"{mins:02d}:{secs:02d}",
                                    "text": text, "translation": translation,
                                    "language": lang, "is_interim": False,
                                    "speaker_label": "", "draft": True,
                                })

                                if not language_detected and lang in ("zh", "ja", "ko", "en"):
                                    detected_lang = lang
                                    language_detected = True
                                    await websocket.send_json({
                                        "type": "status", "status": "language_detected",
                                        "message": f"Detected: {lang}", "language": lang,
                                    })

                            last_transcribe_bytes = total_bytes[0]
                        except Exception as e:
                            logger.error(f"System audio transcription error: {e}")

                    # Send progress
                    if total_bytes[0] > 0:
                        audio_len_s = total_bytes[0] / (16000 * 2)
                        mins = int(audio_len_s // 60)
                        secs = int(audio_len_s % 60)
                        await websocket.send_json({
                            "type": "status", "status": "recording",
                            "message": f"Recording... {mins:02d}:{secs:02d} ({total_bytes[0] // 1024}KB)",
                        })
                    continue
                else:
                    break

            if "bytes" in data:
                audio_bytes = data["bytes"]

                # Write to disk (not RAM)
                wav_file.writeframes(audio_bytes)
                total_bytes[0] += len(audio_bytes)

                # Keep only last 15s in RAM for live transcription
                recent_audio.extend(audio_bytes)
                if len(recent_audio) > 480000:  # 15s at 16kHz 16-bit
                    recent_audio = recent_audio[-480000:]

                audio_len_s = total_bytes[0] / (16000 * 2)

                # Every 8 seconds of audio (~256KB), transcribe the recent chunk
                if total_bytes[0] % 256000 < len(audio_bytes) and is_model_ready():
                    chunk_bytes = bytes(recent_audio)
                    try:
                        result = await asyncio.to_thread(transcribe_audio_bytes, chunk_bytes)
                        text = result.get("text", "")
                        lang = result.get("language", "")

                        if text:
                            line_counter += 1
                            mins = int(audio_len_s // 60)
                            secs = int(audio_len_s % 60)

                            # Live translation to English via Gemini
                            translation = ""
                            if lang in ("zh", "ja", "ko") and text:
                                try:
                                    import requests as req
                                    from dotenv import load_dotenv
                                    load_dotenv(Path(__file__).resolve().parents[5] / ".env")
                                    gkey = os.environ.get("GEMINI_API_KEY", "")
                                    if gkey:
                                        tr_resp = req.post(
                                            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gkey}",
                                            json={
                                                "contents": [{"parts": [{"text": f"Translate this financial meeting transcript segment to English. Keep financial terms. Return ONLY the translation.\n\n{text}"}]}],
                                                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
                                            },
                                            timeout=10,
                                        )
                                        if tr_resp.status_code == 200:
                                            translation = tr_resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                                except Exception:
                                    pass

                            await websocket.send_json({
                                "type": "transcript",
                                "line_id": line_counter,
                                "timestamp": f"{mins:02d}:{secs:02d}",
                                "text": text,
                                "translation": translation,
                                "language": lang,
                                "is_interim": False,
                                "speaker_label": "",
                                "draft": True,
                            })

                            if not language_detected and lang in ("zh", "ja", "ko", "en"):
                                detected_lang = lang
                                language_detected = True
                                await websocket.send_json({
                                    "type": "status", "status": "language_detected",
                                    "message": f"Detected: {lang}",
                                    "language": lang,
                                })
                    except Exception as e:
                        logger.error(f"Live transcription error: {e}")

                # Progress update every 5 seconds
                elif total_bytes[0] % 160000 < len(audio_bytes):
                    mins = int(audio_len_s // 60)
                    secs = int(audio_len_s % 60)
                    status_msg = "Recording..." if is_model_ready() else "Recording (model loading)..."
                    await websocket.send_json({
                        "type": "status", "status": "recording",
                        "message": f"{status_msg} {mins:02d}:{secs:02d}",
                    })

            elif "text" in data:
                try:
                    msg = json.loads(data["text"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if msg.get("type") in ("stop", "stop_no_polish"):
                    if msg.get("type") == "stop_no_polish":
                        # Stop WASAPI thread first
                        stop_event.set()
                        if wasapi_thread is not None:
                            try:
                                wasapi_thread.join(timeout=3)
                            except Exception:
                                pass
                        # Save audio but skip Gemini polish
                        wav_file.close()

                        if total_bytes[0] > 32000:
                            opus_path = wav_path.replace(".wav", ".opus")
                            import subprocess
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus", "-b:a", "48k",
                                 "-ar", "48000", "-ac", "1", opus_path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            )
                            from backend.app.db.session import SessionLocal
                            db = SessionLocal()
                            try:
                                svc = NotesService(db)
                                svc.update_note(note_id, TENANT_ID, recording_path=f"{note_id}.opus")
                            finally:
                                db.close()
                            if os.path.exists(opus_path) and os.path.exists(wav_path):
                                os.unlink(wav_path)

                        await websocket.send_json({
                            "type": "status", "status": "complete",
                            "message": f"Audio saved ({total_bytes[0] // (16000*2)}s). No AI polish.",
                        })
                        return
                    break
                if msg.get("type") == "flag":
                    await websocket.send_json({"type": "flagged", "line_id": msg.get("line_id")})

        # --- Meeting ended ---
        stop_event.set()
        if wasapi_thread is not None and wasapi_thread.is_alive():
            wasapi_thread.join(timeout=3)
        wav_file.close()

        audio_len_s = total_bytes[0] / (16000 * 2)
        await websocket.send_json({
            "type": "status", "status": "processing",
            "message": f"Meeting ended ({audio_len_s:.0f}s audio). Generating polished transcript with Gemini...",
        })

        if total_bytes[0] < 32000:
            await websocket.send_json({"type": "error", "message": "Too little audio recorded. Try again."})
            return

        # Convert to OPUS
        opus_path = wav_path.replace(".wav", ".opus")
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus", "-b:a", "48k",
             "-ar", "48000", "-ac", "1", opus_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Update note recording path
        from backend.app.db.session import SessionLocal
        db = SessionLocal()
        try:
            svc = NotesService(db)
            svc.update_note(note_id, TENANT_ID, recording_path=f"{note_id}.opus")
        finally:
            db.close()

        # Gemini batch transcription
        final_lang = detected_lang if detected_lang != "auto" else "zh"
        source = opus_path if os.path.exists(opus_path) else wav_path

        transcribe_result = await asyncio.to_thread(gemini_batch_transcribe, source, final_lang, note_id)

        if transcribe_result.get("error"):
            await websocket.send_json({
                "type": "error", "message": f"Gemini error: {transcribe_result['error']}",
            })
        else:
            # Stage 2 — cheap text-only Gemini call for the structured summary.
            # Runs only on success of the transcribe stage, on the resulting
            # segments. Kept separate so users can re-run summary later without
            # re-paying the audio cost.
            await websocket.send_json({
                "type": "status", "status": "summarising",
                "message": f"Generating AI summary from {len(transcribe_result.get('segments') or [])} segments...",
            })
            summary_result = await asyncio.to_thread(
                gemini_generate_summary,
                transcribe_result.get("segments") or [],
                transcribe_result.get("language", final_lang),
                note_id,
            )
            # Compose the legacy-shape response dict so the downstream persist
            # / WS send code is unchanged.
            result = {
                **transcribe_result,
                "summary": summary_result.get("summary") or {},
                "input_tokens": transcribe_result.get("input_tokens", 0) + summary_result.get("input_tokens", 0),
                "output_tokens": transcribe_result.get("output_tokens", 0) + summary_result.get("output_tokens", 0),
            }
            # Persist polished transcript + structured segments + summary
            # before notifying the client, so it's durable even if the client
            # disconnects.
            from backend.app.db.session import SessionLocal
            db2 = SessionLocal()
            try:
                svc = NotesService(db2)
                svc.save_polished_transcript(
                    note_id=note_id,
                    tenant_id=TENANT_ID,
                    markdown=result["text"],
                    language=result.get("language", final_lang),
                    meta={
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                        "model": "gemini-2.5-flash",
                        "ran_at": datetime.utcnow().isoformat(),
                        "is_bilingual": result.get("is_bilingual", False),
                        "key_topics": result.get("key_topics", []),
                        "segments": result.get("segments", []),
                        "summary": result.get("summary") or {},
                    },
                )
            finally:
                db2.close()

            await websocket.send_json({
                "type": "polished_transcript",
                "text": result["text"],
                "language": result.get("language", final_lang),
                "is_bilingual": result.get("is_bilingual", False),
                "key_topics": result.get("key_topics", []),
                "segments": result.get("segments", []),
                "summary": result.get("summary") or {},
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
            })
            await websocket.send_json({
                "type": "status", "status": "complete",
                "message": "Polished transcript ready.",
            })

        # Clean up WAV
        if os.path.exists(opus_path) and os.path.exists(wav_path):
            os.unlink(wav_path)

    except WebSocketDisconnect:
        stop_event.set()
        if wasapi_thread is not None:
            try:
                wasapi_thread.join(timeout=3)
            except Exception:
                pass
        wav_file.close()
    except Exception as e:
        stop_event.set()
        if wasapi_thread is not None:
            try:
                wasapi_thread.join(timeout=3)
            except Exception:
                pass
        wav_file.close()
        logger.error(f"Live V2 error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# URL Ingest — populate a note from a YouTube / podcast / video URL
# ---------------------------------------------------------------------------

@router.websocket("/ws/ingest-url/{note_id}")
async def ingest_url_websocket(
    websocket: WebSocket,
    note_id: str,
    url: str = Query(...),
    language: str = Query(default="auto"),
):
    """
    Stream URL-ingest progress + final polished transcript to the client.

    Protocol (identical to the live_v2 recording flow where overlapping):
      server -> client: {type: "status", message: str}
      server -> client: {type: "polished_transcript", text, language,
                         is_bilingual, key_topics, segments, summary,
                         input_tokens, output_tokens}
      server -> client: {type: "status", status: "complete", message: str}
      server -> client: {type: "error", message: str}
    """
    await websocket.accept()

    import logging
    from backend.app.services.url_ingest_service import ingest_url

    logger = logging.getLogger("ingest_url_ws")

    # Re-use the audio dir the recording path uses so we don't sprawl.
    audio_dir = Path(__file__).resolve().parents[5] / "tools" / "audio_recorder" / "recordings"
    audio_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()
    progress_queue: asyncio.Queue = asyncio.Queue()

    def progress_cb(message: str) -> None:
        """Called from the worker thread — hands status strings to the event loop."""
        loop.call_soon_threadsafe(progress_queue.put_nowait, message)

    async def drain_progress_until(done_event: asyncio.Event):
        """Forward any queued progress messages to the client until the worker signals done."""
        while not done_event.is_set() or not progress_queue.empty():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                await websocket.send_json({"type": "status", "message": msg})
            except asyncio.TimeoutError:
                continue

    try:
        done = asyncio.Event()
        result_holder: dict = {}
        error_holder: dict = {}

        def worker():
            try:
                result_holder["result"] = ingest_url(
                    url=url,
                    note_id=note_id,
                    language_hint=language,
                    audio_dir=audio_dir,
                    progress=progress_cb,
                )
            except Exception as exc:
                logger.exception("URL ingest failed")
                error_holder["error"] = str(exc)
            finally:
                loop.call_soon_threadsafe(done.set)

        worker_task = asyncio.create_task(asyncio.to_thread(worker))
        drain_task = asyncio.create_task(drain_progress_until(done))

        await worker_task
        await drain_task

        if "error" in error_holder:
            await websocket.send_json({"type": "error", "message": error_holder["error"]})
            return

        result = result_holder.get("result") or {}
        if result.get("error"):
            await websocket.send_json({"type": "error", "message": result["error"]})
            return

        # Persist polished transcript + summary + source_url.
        from backend.app.db.session import SessionLocal
        db2 = SessionLocal()
        try:
            svc = NotesService(db2)
            svc.save_polished_transcript(
                note_id=note_id,
                tenant_id=TENANT_ID,
                markdown=result.get("text", ""),
                language=result.get("language", language),
                meta={
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "model": "gemini-2.5-flash",
                    "ran_at": datetime.utcnow().isoformat(),
                    "is_bilingual": result.get("is_bilingual", False),
                    "key_topics": result.get("key_topics", []),
                    "segments": result.get("segments", []),
                    "summary": result.get("summary") or {},
                    "source_url": url,
                },
            )
            svc.update_note(note_id, TENANT_ID, source_url=url)
        finally:
            db2.close()

        await websocket.send_json({
            "type": "polished_transcript",
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "is_bilingual": result.get("is_bilingual", False),
            "key_topics": result.get("key_topics", []),
            "segments": result.get("segments", []),
            "summary": result.get("summary") or {},
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        })
        await websocket.send_json({
            "type": "status", "status": "complete",
            "message": "URL ingest complete.",
        })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("URL ingest WS error")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
