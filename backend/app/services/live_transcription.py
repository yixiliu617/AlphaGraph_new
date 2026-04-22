"""
Live Transcription Service — Language-aware model routing.

Architecture (Option B):
  DURING MEETING:
    Audio stream → Language detection (SenseVoice, 3s) → Route to best live ASR
      ZH/EN → SenseVoice streaming (best for Chinese)
      JA/EN → kotoba-whisper chunked (best for Japanese)
      KO/EN → Whisper large-v3 chunked (best for Korean)
      EN    → Whisper large-v3 or SenseVoice
    → Live draft transcript displayed to user

  AFTER MEETING:
    Full audio file → Gemini 2.5 Flash native audio (V2 quality)
    → Polished final transcript replaces draft
"""

import asyncio
import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

VOCAB_DIR = Path(__file__).resolve().parents[2] / "tools" / "audio_recorder"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_vocabulary(language: str = "zh") -> str:
    """Load the appropriate vocabulary file for the detected language."""
    vocab_files = {
        "zh": VOCAB_DIR / "meeting_vocabulary.json",
        "ja": VOCAB_DIR / "meeting_vocabulary_ja.json",
        "ko": VOCAB_DIR / "meeting_vocabulary_ko.json",
    }

    path = vocab_files.get(language, vocab_files["zh"])
    if not path.exists():
        path = vocab_files["zh"]  # fallback to Chinese vocab
    if not path.exists():
        return ""

    with open(path, "r", encoding="utf-8") as f:
        vocab = json.load(f)

    vc = "VOCABULARY:\n"
    for sector, companies in vocab.get("companies", {}).items():
        for c in companies:
            line = f'- {c["zh"]} ({c["en"]}, {c["ticker"]})'
            if "notes" in c:
                line += f' NOTE: {c["notes"]}'
            vc += line + "\n"

    vc += "\nCRITICAL MISRECOGNITIONS:\n"
    for m in vocab.get("common_misrecognitions", []):
        vc += f'- WRONG: {m["wrong"]} -> CORRECT: {m["correct"]}\n'

    return vc


def detect_language_from_audio(audio_bytes: bytes, sample_rate: int = 16000) -> str:
    """Detect language from a short audio sample using SenseVoice."""
    try:
        import tempfile
        import soundfile as sf
        import numpy as np
        from funasr import AutoModel

        # Save to temp WAV
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, audio_array, sample_rate)
        tmp.close()

        model = AutoModel(
            model="iic/SenseVoiceSmall",
            device="cpu",
            disable_update=True,
        )

        result = model.generate(input=tmp.name, cache={}, language="auto", use_itn=False)
        os.unlink(tmp.name)

        if result:
            text = result[0].get("text", "")
            # SenseVoice tags: <|zh|>, <|en|>, <|ja|>, <|ko|>, <|yue|>
            lang_match = re.search(r"<\|(\w+)\|>", text)
            if lang_match:
                lang = lang_match.group(1)
                if lang in ("zh", "yue"):
                    return "zh"
                elif lang == "ja":
                    return "ja"
                elif lang == "ko":
                    return "ko"
                elif lang == "en":
                    return "en"
        return "zh"  # default
    except Exception:
        return "zh"


class LiveTranscriber:
    """Manages live transcription with language-aware model routing."""

    def __init__(self, on_transcript: Callable, on_status: Callable):
        """
        on_transcript(line_id, timestamp, text, is_interim, speaker) — called for each transcript line
        on_status(status, message) — called for status updates
        """
        self.on_transcript = on_transcript
        self.on_status = on_status
        self.detected_language: str = "zh"
        self.line_counter = 0
        self.stop_event = threading.Event()
        self.audio_buffer = bytearray()
        self._model = None
        self._started = False

    def detect_language(self, audio_sample: bytes):
        """Detect language from initial audio sample."""
        self.detected_language = detect_language_from_audio(audio_sample)
        self.on_status("language_detected", f"Detected: {self.detected_language}")

    def start_sensevoice_streaming(self):
        """Start SenseVoice for Chinese/EN live transcription."""
        try:
            from funasr import AutoModel

            self.on_status("loading", "Loading SenseVoice model...")
            self._model = AutoModel(
                model="iic/SenseVoiceSmall",
                vad_model="fsmn-vad",
                vad_kwargs={"max_single_segment_time": 15000},
                device="cpu",
                disable_update=True,
            )
            self._started = True
            self.on_status("ready", "SenseVoice ready (Chinese/English)")
        except Exception as e:
            self.on_status("error", f"Failed to load SenseVoice: {e}")

    def process_audio_chunk(self, audio_bytes: bytes):
        """Process an audio chunk — accumulate and periodically transcribe."""
        self.audio_buffer.extend(audio_bytes)

        # Transcribe every ~5 seconds of audio (5 * 16000 * 2 bytes = 160KB)
        if len(self.audio_buffer) >= 160000:
            self._transcribe_buffer()

    def _transcribe_buffer(self):
        """Transcribe accumulated audio buffer."""
        if not self._model or len(self.audio_buffer) < 32000:
            return

        try:
            import tempfile
            import soundfile as sf
            import numpy as np

            audio = np.frombuffer(bytes(self.audio_buffer), dtype=np.int16).astype(np.float32) / 32768.0
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            sf.write(tmp.name, audio, 16000)
            tmp.close()

            result = self._model.generate(
                input=tmp.name, cache={}, language="auto", use_itn=True, batch_size_s=30,
            )
            os.unlink(tmp.name)

            if result:
                text = result[0].get("text", "")
                clean = re.sub(r"<\|[^|]+\|>", "", text).strip()
                if clean:
                    self.line_counter += 1
                    import datetime as dt
                    ts = dt.datetime.now().strftime("%H:%M:%S")
                    self.on_transcript(self.line_counter, ts, clean, False, "")

            # Keep last 1 second as overlap for continuity
            self.audio_buffer = self.audio_buffer[-32000:]

        except Exception as e:
            self.on_status("error", f"Transcription error: {e}")

    def flush(self):
        """Process any remaining audio in the buffer."""
        if len(self.audio_buffer) > 32000:
            self._transcribe_buffer()

    def stop(self):
        """Stop live transcription."""
        self.stop_event.set()
        self.flush()
        self._model = None
        self._started = False


async def gemini_batch_transcribe(
    audio_path: str,
    language: str = "zh",
    note_id: str = "",
) -> dict:
    """
    Run Gemini V2-quality batch transcription on the full audio file.
    Returns {"text": ..., "key_topics": [...], "speakers": [...]}
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set", "text": ""}

    vocab_context = load_vocabulary(language)

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    # Determine mime type
    ext = Path(audio_path).suffix.lower()
    mime_types = {".opus": "audio/ogg", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
    mime = mime_types.get(ext, "audio/ogg")

    lang_names = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}
    lang_name = lang_names.get(language, "Chinese")

    prompt = f"""{vocab_context}
Transcribe this financial meeting audio. Primary language: {lang_name} with English code-switching.

Rules:
1. Exact transcription with speaker names + timestamps [MM:SS]
2. Bold **key data points**
3. Key topics list at top
4. CRITICAL: NEVER repeat the same phrase more than once. If audio unclear, write [audio unclear] and skip ahead."""

    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    resp = requests.post(
        url,
        json={
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": audio_b64}},
            ]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 65536},
        },
        timeout=900,
    )

    if resp.status_code != 200:
        return {"error": f"Gemini API error: {resp.status_code}", "text": ""}

    result = resp.json()
    text = result["candidates"][0]["content"]["parts"][0]["text"]

    # Clean repetition loops
    text = re.sub(r"(.{10,50}?)\1{3,}", r"\1", text)

    usage = result.get("usageMetadata", {})

    return {
        "text": text,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
        "language": language,
    }
