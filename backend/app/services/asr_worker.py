"""
ASR Worker — runs SenseVoice in a background thread with a job queue.
Model loads ONCE at import time, not per-WebSocket connection.
"""

import os
import re
import queue
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

_model = None
_model_lock = threading.Lock()
_model_loading = False


def _get_model():
    """Lazy-load SenseVoice model (once, thread-safe)."""
    global _model, _model_loading
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        _model_loading = True
        try:
            from funasr import AutoModel
            _model = AutoModel(
                model="iic/SenseVoiceSmall",
                vad_model="fsmn-vad",
                vad_kwargs={"max_single_segment_time": 15000},
                device="cpu",
                disable_update=True,
            )
        except Exception as e:
            print(f"ASR Worker: Failed to load SenseVoice: {e}")
            _model = None
        _model_loading = False
        return _model


def is_model_ready():
    return _model is not None


def is_model_loading():
    return _model_loading


def transcribe_audio_bytes(audio_bytes: bytes, sample_rate: int = 16000) -> dict:
    """
    Transcribe raw PCM16 audio bytes. Returns {"text": ..., "language": ...}.
    Thread-safe — can be called from any thread.
    """
    model = _get_model()
    if model is None:
        return {"text": "", "language": "unknown", "error": "Model not loaded"}

    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio_array) < 1600:  # less than 0.1s
        return {"text": "", "language": "unknown"}

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, audio_array, sample_rate)
    tmp.close()

    try:
        result = model.generate(
            input=tmp.name, cache={}, language="auto",
            use_itn=True, batch_size_s=30,
        )
    finally:
        os.unlink(tmp.name)

    if not result:
        return {"text": "", "language": "unknown"}

    text = result[0].get("text", "")
    lang_match = re.search(r"<\|(\w+)\|>", text)
    lang = lang_match.group(1) if lang_match else "unknown"
    if lang == "yue":
        lang = "zh"
    clean = re.sub(r"<\|[^|]+\|>", "", text).strip()

    return {"text": clean, "language": lang}


# Pre-load model in background thread at import time
def _preload():
    _get_model()

_preload_thread = threading.Thread(target=_preload, daemon=True)
_preload_thread.start()
