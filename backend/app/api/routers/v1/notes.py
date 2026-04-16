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
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_llm_provider, get_db_repo
from backend.app.db.session import get_db_session
from backend.app.models.api_contracts import APIResponse
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


class UpdateNoteRequest(BaseModel):
    title: Optional[str] = None
    editor_content: Optional[dict] = None
    editor_plain_text: Optional[str] = None
    company_tickers: Optional[List[str]] = None
    meeting_date: Optional[str] = None


class FlagLineRequest(BaseModel):
    line_id: int
    flagged: bool


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
    updated = svc.update_note(
        note_id, TENANT_ID,
        title=request.title,
        editor_content=request.editor_content,
        editor_plain_text=request.editor_plain_text,
        company_tickers=request.company_tickers if request.company_tickers else None,
        meeting_date=request.meeting_date,
    )
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


@router.post("/{note_id}/transcript/flag", response_model=APIResponse)
def flag_transcript_line(note_id: str, request: FlagLineRequest, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    updated = svc.flag_transcript_line(note_id, TENANT_ID, request.line_id, request.flagged)
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found.")
    return APIResponse(success=True, data={"line_id": request.line_id, "flagged": request.flagged})


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
    if not request.topics:
        raise HTTPException(status_code=400, detail="Provide at least one topic.")
    svc = MeetingSummaryService(db, db_repo, llm)
    try:
        note = svc.extract_topic_fragments(note_id, TENANT_ID, request.topics)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return APIResponse(success=True, data=note.model_dump())


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
