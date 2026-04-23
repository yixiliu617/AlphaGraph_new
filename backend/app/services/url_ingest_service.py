"""
URL ingest service — populate a MeetingNote from a YouTube / podcast / video URL.

Captions-first, audio-fallback:
  1. If yt-dlp reports manual captions in a supported language, download the
     VTT and feed the resulting text to gemini_polish_text (fast + cheap).
  2. Otherwise, download audio via yt-dlp and run the existing
     gemini_batch_transcribe (existing path, slower + a Gemini audio call).

Auto-generated captions are intentionally skipped — quality is too unreliable
for analyst workflows; the audio fallback is cheap enough.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Optional

import requests
import yt_dlp

logger = logging.getLogger(__name__)

# Languages we accept from the subtitles dict, in preference order.
_ACCEPTED_LANGS = ("en", "ja", "zh", "zh-Hans", "zh-Hant", "ko")


# ---------------------------------------------------------------------------
# VTT parsing
# ---------------------------------------------------------------------------

_VTT_TIMESTAMP = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{1,2}:\d{2}:\d{2}\.\d{3})"
)

# Strip simple HTML/tagged styling like <c.colorBBBBBB>, <b>, <i>, etc.
_VTT_TAG = re.compile(r"<[^>]+>")


def _hms_to_mmss(hms: str) -> str:
    """Convert 'HH:MM:SS.mmm' to 'MM:SS' (dropping milliseconds), collapsing
    the hour into minutes if non-zero."""
    try:
        hh, mm, rest = hms.split(":")
        ss = rest.split(".")[0]
        total_min = int(hh) * 60 + int(mm)
        return f"{total_min:02d}:{int(ss):02d}"
    except Exception:
        return hms


def _parse_vtt(vtt_text: str) -> list[dict]:
    """Parse a WEBVTT payload into the segment shape that the rest of the
    ingest pipeline expects: [{timestamp, speaker, text_original, text_english}].

    We leave text_english empty — if the caller wants bilingual output, it
    passes the segments through gemini_polish_text which will populate
    text_english via translation."""
    segments: list[dict] = []
    lines = vtt_text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()
        i += 1

        m = _VTT_TIMESTAMP.match(line)
        if not m:
            continue

        ts = _hms_to_mmss(m.group("start"))
        # Collect all non-empty lines until a blank (end of cue).
        buf: list[str] = []
        while i < n:
            cue_line = lines[i].rstrip()
            if not cue_line.strip():
                break
            buf.append(_VTT_TAG.sub("", cue_line).strip())
            i += 1

        text = " ".join(x for x in buf if x).strip()
        if text:
            segments.append({
                "timestamp": ts,
                "speaker": "",
                "text_original": text,
                "text_english": "",
            })

    return segments


# ---------------------------------------------------------------------------
# yt-dlp wrappers
# ---------------------------------------------------------------------------

def try_fetch_manual_captions(url: str, lang_hint: str = "auto") -> Optional[dict]:
    """Check whether the video has **manual** (creator-uploaded) captions in
    an accepted language. If so, download the VTT and parse it.

    Returns {"language": str, "segments": list[dict]} or None.

    NEVER returns auto-generated captions (Q1=b per user decision).
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("yt-dlp manual-captions probe failed for %s: %s", url, exc)
        return None

    subs = (info or {}).get("subtitles") or {}
    if not subs:
        return None

    # Pick a language: honour lang_hint if present, else scan accepted langs in order.
    candidates = []
    if lang_hint and lang_hint != "auto":
        candidates.append(lang_hint)
    candidates.extend(_ACCEPTED_LANGS)

    picked_lang: Optional[str] = None
    picked_entries: list[dict] = []
    for cand in candidates:
        # Match exact or with prefix (e.g. "en-US" when we asked for "en").
        for lang_key, entries in subs.items():
            if lang_key == cand or lang_key.startswith(f"{cand}-"):
                picked_lang = lang_key
                picked_entries = entries or []
                break
        if picked_lang:
            break

    if not picked_lang:
        return None

    # Prefer VTT, fall back to the first available format.
    vtt_entry = next((e for e in picked_entries if e.get("ext") == "vtt"), None)
    entry = vtt_entry or (picked_entries[0] if picked_entries else None)
    if not entry or not entry.get("url"):
        return None

    try:
        resp = requests.get(entry["url"], timeout=30)
        if resp.status_code != 200:
            logger.warning("Manual-captions download failed: HTTP %d", resp.status_code)
            return None
        segments = _parse_vtt(resp.text)
    except Exception as exc:
        logger.warning("Manual-captions fetch/parse failed: %s", exc)
        return None

    if not segments:
        return None

    # Normalise language to 2-letter ISO for the rest of the pipeline.
    norm_lang = picked_lang.split("-")[0].lower()
    return {"language": norm_lang, "segments": segments}


def download_audio(url: str, out_path: Path) -> str:
    """Download the audio track of `url` to an OPUS file at `out_path`
    (the caller provides the path WITHOUT extension; yt-dlp appends .opus).
    Returns the final path including extension.

    Raises `RuntimeError` if the download fails.
    """
    # yt-dlp will write `{out_path}.opus`.
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(out_path),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "opus",
            "preferredquality": "48",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise RuntimeError(f"yt-dlp audio download failed: {exc}") from exc

    final_path = f"{out_path}.opus"
    if not Path(final_path).exists():
        raise RuntimeError(f"yt-dlp claimed success but {final_path} is missing")
    return final_path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str], None]


def ingest_url(
    url: str,
    note_id: str,
    language_hint: str,
    audio_dir: Path,
    progress: ProgressCallback,
) -> dict:
    """End-to-end URL ingest. Two-stage pipeline:

    Stage 1 — get transcript segments:
      (a) Manual captions via yt-dlp, OR
      (b) Audio download + Gemini audio transcription.

    Stage 2 — summary: text-only Gemini call over the segments from stage 1.
    Cheap (~$0.001-0.01) and re-runnable without touching audio.

    Returns the full combined dict (language, is_bilingual, key_topics,
    segments, summary, text, input_tokens, output_tokens)."""
    from backend.app.services.live_transcription import (
        gemini_batch_transcribe,
        gemini_generate_summary,
        _flatten_segments_to_markdown,
        _empty_summary,
    )

    progress("Checking for manual captions...")
    captions = try_fetch_manual_captions(url, language_hint)

    if captions:
        # Stage 1a — captions.
        n = len(captions["segments"])
        lang = captions["language"]
        progress(f"Manual captions found ({lang}, {n} segments).")
        segments = captions["segments"]
        # Captions arrive with text_english empty; since summary generation
        # prefers text_english over text_original, mirror text_original when
        # the source language is English so the summary has something to read.
        if lang == "en":
            for s in segments:
                if not s.get("text_english"):
                    s["text_english"] = s.get("text_original", "")
        is_bilingual = lang != "en"  # captions don't auto-translate; summary below still works off text_original
        key_topics: list = []
        transcribe_tokens_in = 0
        transcribe_tokens_out = 0
    else:
        # Stage 1b — audio download + transcribe.
        progress("No manual captions. Downloading audio (this may take ~30s)...")
        out_stem = audio_dir / f"{note_id}_url"
        audio_path = download_audio(url, out_stem)

        progress("Audio downloaded. Running Gemini transcription (can take 1-5 min)...")
        final_lang = language_hint if language_hint in ("zh", "ja", "ko", "en") else "en"
        transcribe_result = gemini_batch_transcribe(audio_path, final_lang, note_id)

        if transcribe_result.get("error"):
            # Bubble up the error without running summary on an empty transcript.
            return {
                "error": transcribe_result["error"],
                "language": final_lang,
                "is_bilingual": False,
                "key_topics": [],
                "segments": [],
                "summary": _empty_summary(),
                "text": "",
                "input_tokens": transcribe_result.get("input_tokens", 0),
                "output_tokens": transcribe_result.get("output_tokens", 0),
            }

        segments = transcribe_result.get("segments", []) or []
        lang = transcribe_result.get("language", final_lang)
        is_bilingual = bool(transcribe_result.get("is_bilingual", False))
        key_topics = transcribe_result.get("key_topics", []) or []
        transcribe_tokens_in = transcribe_result.get("input_tokens", 0)
        transcribe_tokens_out = transcribe_result.get("output_tokens", 0)

    # Stage 2 — summary (text-only, cheap).
    progress(f"Generating AI summary from {len(segments)} segments...")
    summary_result = gemini_generate_summary(
        segments=segments,
        language_hint=lang,
        note_id=note_id,
    )
    summary = summary_result.get("summary") or _empty_summary()
    summary_tokens_in = summary_result.get("input_tokens", 0)
    summary_tokens_out = summary_result.get("output_tokens", 0)

    # Build the flattened markdown form for polished_transcript storage / export.
    text_md = _flatten_segments_to_markdown(segments, is_bilingual) if segments else ""

    return {
        "language": lang,
        "is_bilingual": is_bilingual,
        "key_topics": key_topics,
        "segments": segments,
        "summary": summary,
        "text": text_md,
        "input_tokens": transcribe_tokens_in + summary_tokens_in,
        "output_tokens": transcribe_tokens_out + summary_tokens_out,
    }
