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
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
from pathlib import Path
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

VOCAB_DIR = Path(__file__).resolve().parents[2] / "tools" / "audio_recorder"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ----- Long-audio normalization & splitting thresholds ---------------------
# Sized for Gemini 2.5 Flash's ~20 MB inline_data ceiling (post-base64),
# AND for Gemini's tendency to "get lazy" mid-chunk on long audio. The
# 81-min Softbank file (2026-04-26) silently skipped 37 minutes of audio
# in chunk 1 of a 2x40-min split -- Gemini's chunk-1 output truncated at
# segment 12 of ~70 expected. Going to ~27-min chunks reduces the per-chunk
# token budget Gemini has to spend, which makes the lazy mode much rarer.
#
#   bitrate     | size/min  |  base64/min  | inline-safe upper bound
#   48 kbps     | ~360 kB   | ~480 kB      | ~40 min
#   24 kbps     | ~180 kB   | ~240 kB      | ~80 min
#
# Both bitrates fit any chunk size we'll produce.
_BITRATE_CUTOFF_MIN   = 40       # < this: encode at 48 kbps; >= this: 24 kbps
_SPLIT_THRESHOLD_MIN  = 30       # only split if audio > this (no point splitting 31-min into 2x15 only marginally)
_TARGET_CHUNK_MIN     = 27       # each chunk targets ~this many minutes
_SILENCE_THRESHOLD_DB = -30      # ffmpeg silencedetect noise threshold (dB)
_SILENCE_MIN_SEC      = 0.6      # minimum silence to count as a pause candidate
_SILENCE_SEARCH_RADIUS_MIN = 5   # search ±this many minutes around each target split for a silence
_COVERAGE_GAP_MIN_SEC = 300      # consecutive segments >this apart -> flag a coverage gap (5 min)


def _ffprobe_duration(path: Path) -> float:
    """Return audio file duration in seconds using ffprobe. Raises on failure."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


def _normalize_audio_for_meeting(input_path: Path, work_dir: Path) -> tuple[Path, float]:
    """Re-encode any input audio to mono 16 kHz Opus.

    Bitrate is duration-aware:
      < _BITRATE_CUTOFF_MIN minutes -> 48 kbps
      >= _BITRATE_CUTOFF_MIN minutes -> 24 kbps (Opus VoIP profile, transparent for speech)

    Returns (normalized_path, duration_seconds). The output is always written
    fresh under work_dir; idempotent re-encoding (Opus->Opus) is harmless and
    keeps the post-pipeline filename predictable.
    """
    duration = _ffprobe_duration(input_path)
    bitrate = "48k" if duration < _BITRATE_CUTOFF_MIN * 60 else "24k"
    out = work_dir / f"{input_path.stem}_norm.opus"

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-c:a", "libopus", "-b:a", bitrate,
        "-application", "voip",
        "-ar", "16000", "-ac", "1",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg normalize failed: {proc.stderr[-400:].strip()}")
    logger.info("normalize_audio: %s (%.1fs) -> %s @ %s",
                input_path.name, duration, out.name, bitrate)
    return out, duration


def _find_silence_split_points(audio_path: Path, n_chunks: int) -> list[float]:
    """Return n_chunks-1 split points (in seconds) chosen near LONG silences.

    Score-based selection so we prefer end-of-sentence / end-of-paragraph
    pauses over short between-clause pauses near the target:

        score = silence_duration_sec - 0.4 * abs(midpoint_sec - target_sec) / 60

    For every silence within +/- _SILENCE_SEARCH_RADIUS_MIN minutes of the
    ideal split target, compute that score and pick the highest. The
    duration weight pushes us toward sentence boundaries; the distance
    weight keeps chunks roughly even. Empirically:
      - a 2.0-sec pause 60 sec from target  ->  score 2.0 - 0.4*1 = 1.6
      - a 0.7-sec pause AT target           ->  score 0.7 - 0   = 0.7
    The 2-sec pause wins, even though it's 1 min off-target.

    Edge cases handled:
      - No silence within +/- search radius -> hard-cut at the target
        (rare; only happens with continuous talking like a single-speaker
         podcast read at a steady pace).
      - Multiple targets close together (e.g. 4 chunks in a 90-min file)
        -> each target picks its own best silence independently; we de-dupe
           if the same silence wins for two targets, falling back to
           hard-cut for the second.
    """
    if n_chunks <= 1:
        return []

    duration = _ffprobe_duration(audio_path)
    target_sec = duration / n_chunks
    targets = [target_sec * (i + 1) for i in range(n_chunks - 1)]

    cmd = [
        "ffmpeg", "-i", str(audio_path),
        "-af", f"silencedetect=noise={_SILENCE_THRESHOLD_DB}dB:d={_SILENCE_MIN_SEC}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    log = proc.stderr or ""

    silence_windows: list[tuple[float, float, float]] = []   # (start, end, duration)
    cur_start: Optional[float] = None
    for line in log.splitlines():
        m_start = re.search(r"silence_start:\s*([\d.]+)", line)
        m_end   = re.search(r"silence_end:\s*([\d.]+)", line)
        if m_start:
            cur_start = float(m_start.group(1))
        elif m_end and cur_start is not None:
            end = float(m_end.group(1))
            silence_windows.append((cur_start, end, end - cur_start))
            cur_start = None

    if not silence_windows:
        logger.warning("silencedetect found no pauses; hard cuts at %s",
                       [f"{t:.0f}s" for t in targets])
        return targets

    radius_sec = _SILENCE_SEARCH_RADIUS_MIN * 60
    used_indices: set[int] = set()
    splits: list[float] = []
    for t in targets:
        # Score every silence within +/- radius of the target.
        candidates: list[tuple[float, int, float]] = []   # (score, idx, midpoint)
        for i, (start, end, dur) in enumerate(silence_windows):
            if i in used_indices:
                continue
            mid = (start + end) / 2
            if abs(mid - t) > radius_sec:
                continue
            score = dur - 0.4 * abs(mid - t) / 60.0
            candidates.append((score, i, mid))

        if not candidates:
            logger.warning("no silence within +/-%dmin of target %.0fs; hard cut",
                           _SILENCE_SEARCH_RADIUS_MIN, t)
            splits.append(t)
            continue

        # Highest score wins.
        score, idx, mid = max(candidates, key=lambda c: c[0])
        used_indices.add(idx)
        s_start, s_end, s_dur = silence_windows[idx]
        splits.append(mid)
        logger.info(
            "split target=%.0fs -> silence at %.0fs-%.0fs (dur=%.2fs, score=%.2f, off-target=%.0fs)",
            t, s_start, s_end, s_dur, score, mid - t,
        )

    return splits


def _split_audio_at(
    audio_path: Path, split_points: list[float], work_dir: Path,
) -> list[tuple[Path, float, float]]:
    """Cut audio_path into N chunks at each split point. Returns list of
    (chunk_path, start_sec, end_sec).

    Always re-encodes (no `-c copy`) so cuts are byte-precise. Stream-copy
    can shift the cut by up to the previous keyframe (~5 sec for some Opus
    container chains), which would mean the next chunk overlaps the last
    one's tail and Gemini transcribes the same audio twice. Re-encoding is
    cheap on already-normalized 24/48kbps mono Opus (sub-second per chunk).
    """
    duration = _ffprobe_duration(audio_path)
    boundaries = [0.0, *split_points, duration]
    chunks: list[tuple[Path, float, float]] = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        out = work_dir / f"{audio_path.stem}_chunk{i+1}.opus"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", str(audio_path),
            "-c:a", "libopus", "-b:a", "24k",
            "-application", "voip", "-ar", "16000", "-ac", "1",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"split_audio failed for chunk {i+1}: {proc.stderr[-300:]}")
        chunks.append((out, start, end))
    logger.info("split_audio: produced %d chunks: %s",
                len(chunks), [(c[0].name, f"{c[1]:.0f}-{c[2]:.0f}s") for c in chunks])
    return chunks


# ---- Timestamp shifting for chunk merging ---------------------------------
_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+)(?:[:.](\d+))?\s*$")


def _parse_ts_to_seconds(ts: str) -> Optional[float]:
    """Parse 'MM:SS', 'MM:SS.cc', 'HH:MM:SS', etc. Returns total seconds or None."""
    if not ts:
        return None
    m = _TS_RE.match(str(ts))
    if not m:
        return None
    h, mm, ss, frac = m.groups()
    seconds = int(mm) * 60 + int(ss)
    if h is not None:
        seconds += int(h) * 3600
    if frac:
        seconds += int(frac) / (10 ** len(frac))
    return float(seconds)


def _format_seconds_as_ts(total_seconds: float, total_duration_sec: float) -> str:
    """Format MM:SS for sub-1-hour audio, HH:MM:SS for >= 1 hour total."""
    s = max(0, int(round(total_seconds)))
    if total_duration_sec >= 3600:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"
    m, sec = divmod(s, 60)
    return f"{m:02d}:{sec:02d}"


def _offset_segments(segments: list[dict], offset_sec: float, total_dur: float) -> list[dict]:
    """Shift every segment's `timestamp` field by `offset_sec`. Format choice
    depends on total merged duration so timestamps stay readable."""
    out = []
    for seg in segments:
        t = _parse_ts_to_seconds(seg.get("timestamp", ""))
        new_seg = dict(seg)
        if t is not None:
            new_seg["timestamp"] = _format_seconds_as_ts(t + offset_sec, total_dur)
        out.append(new_seg)
    return out


def _estimate_segment_duration_sec(seg: dict) -> float:
    """Rough estimate of how many seconds of audio a single segment covers.

    Used by _detect_coverage_gaps so a long monologue segment ("speaker A
    talked for 6 straight minutes, Gemini emitted one big segment") doesn't
    get falsely flagged as an audio coverage gap. We use the LONGER of
    text_original / text_english divided by ~10 chars/sec -- a generous
    floor (real speech is faster) so we err toward NOT flagging when the
    transcript is plausible.

    Calibration:
      - English at 150 wpm ≈ 13 chars/sec
      - Chinese / Japanese ≈ 3-4 chars/sec (each char is one syllable)
      - Bilingual (zh/ja) outputs put text_english alongside text_original;
        text_english tends to be the longer one in char count, which dominates.
      - 10 chars/sec is conservative against false positives but still leaves
        any 5+ min real gap clearly visible (estimated <1 min for a typical
        300-char segment).
    """
    text  = seg.get("text_original", "") or ""
    en    = seg.get("text_english",  "") or ""
    chars = max(len(text), len(en))
    return max(2.0, chars / 10.0)


def _detect_coverage_gaps(
    segments: list[dict],
    total_duration_sec: float,
    *,
    gap_min_sec: float = _COVERAGE_GAP_MIN_SEC,
) -> list[dict]:
    """Find suspicious gaps between consecutive segments and at the tail.

    Catches the Gemini "got lazy" failure mode where the model silently
    skips a chunk of audio and produces a sparse transcript. Three flavors:

      kind="lead"      - audio before the first segment (e.g. silence at
                          start) > gap_min_sec
      kind="middle"    - audio between consecutive segments unaccounted for
                          even after estimating the previous segment's own
                          duration from its text length. Catches Gemini
                          dropping 30 minutes of audio (real failure) while
                          ignoring long monologue segments where one segment
                          legitimately covers 6+ minutes (false positive).
      kind="tail"      - audio after the last segment's estimated end.

    Each gap dict carries enough info for the UI to call
    POST /notes/{id}/retranscribe-from?start_seconds=<gap.start_sec>:
        {kind, start_sec, end_sec, duration_sec, start_label, end_label}
    """
    gaps: list[dict] = []

    def _label(sec: float) -> str:
        return _format_seconds_as_ts(sec, total_duration_sec)

    # Parse + estimate per-segment duration. Segments without parseable
    # timestamps are skipped so they don't shift the gap math.
    valid: list[tuple[dict, float, float]] = []   # (seg, start_sec, est_dur_sec)
    for seg in segments:
        ts = _parse_ts_to_seconds(seg.get("timestamp", ""))
        if ts is not None:
            valid.append((seg, ts, _estimate_segment_duration_sec(seg)))

    if not valid:
        # No segments at all -> the entire audio is one big gap.
        if total_duration_sec > gap_min_sec:
            gaps.append({
                "kind":         "tail",
                "start_sec":    0.0,
                "end_sec":      total_duration_sec,
                "duration_sec": total_duration_sec,
                "start_label":  _label(0.0),
                "end_label":    _label(total_duration_sec),
            })
        return gaps

    # Lead: audio before the first segment starts.
    first_ts = valid[0][1]
    if first_ts > gap_min_sec:
        gaps.append({
            "kind":         "lead",
            "start_sec":    0.0,
            "end_sec":      first_ts,
            "duration_sec": first_ts,
            "start_label":  _label(0.0),
            "end_label":    _label(first_ts),
        })

    # Middle: audio between consecutive segments NOT explained by the prior
    # segment's estimated duration.
    for i in range(1, len(valid)):
        _, prev_ts, prev_dur = valid[i - 1]
        _, cur_ts, _         = valid[i]
        prev_est_end = prev_ts + prev_dur
        unaccounted  = cur_ts - prev_est_end
        if unaccounted > gap_min_sec:
            # Anchor the reported gap at the prior segment's end estimate so
            # the "Retranscribe from..." button starts there, not in the
            # middle of the prior segment's content.
            gap_start = max(prev_ts, prev_est_end)
            gaps.append({
                "kind":         "middle",
                "start_sec":    gap_start,
                "end_sec":      cur_ts,
                "duration_sec": cur_ts - gap_start,
                "start_label":  _label(gap_start),
                "end_label":    _label(cur_ts),
            })

    # Tail: audio after the last segment's estimated end.
    _, last_ts, last_dur = valid[-1]
    last_end = last_ts + last_dur
    if total_duration_sec - last_end > gap_min_sec:
        gaps.append({
            "kind":         "tail",
            "start_sec":    last_end,
            "end_sec":      total_duration_sec,
            "duration_sec": total_duration_sec - last_end,
            "start_label":  _label(last_end),
            "end_label":    _label(total_duration_sec),
        })

    return gaps


def _merge_chunk_results(
    results: list[tuple[dict, float, float]],
    fallback_language: str,
) -> dict:
    """Combine N chunk transcribe results into one bigger one matching the
    shape gemini_batch_transcribe normally returns.

    `results` is a list of (chunk_result_dict, start_sec, end_sec). Segments
    from chunk i get their timestamps offset by chunk_i.start_sec; key_topics
    are unioned (dedup, preserve first-seen order); usage tokens summed.
    Per-chunk timing (`gemini_seconds`) is summed into a `gemini_seconds`
    field on the merged result, with `chunk_seconds` keeping the per-chunk
    breakdown for diagnostics.
    """
    if not results:
        return {
            "language":      fallback_language,
            "is_bilingual":  False,
            "key_topics":    [],
            "segments":      [],
            "summary":       _empty_summary(),
            "text":          "",
            "input_tokens":  0,
            "output_tokens": 0,
            "gemini_seconds": 0.0,
            "chunk_count":    0,
            "chunk_seconds":  [],
        }

    total_dur = max(end for _, _, end in results)
    merged_segments: list[dict] = []
    merged_topics:   list[str]  = []
    seen_topics:     set[str]   = set()
    in_tokens = 0
    out_tokens = 0
    languages: list[str]   = []
    bilingual_flags: list[bool] = []
    gemini_total = 0.0
    chunk_seconds: list[float] = []
    successful_chunks = 0

    for r, start, _end in results:
        if r.get("error"):
            logger.warning("chunk transcribe error at offset %.0fs: %s", start, r["error"])
            chunk_seconds.append(round(float(r.get("gemini_seconds", 0.0)), 2))
            continue
        merged_segments.extend(_offset_segments(r.get("segments", []), start, total_dur))
        for topic in r.get("key_topics", []) or []:
            key = (topic or "").strip()
            if key and key not in seen_topics:
                merged_topics.append(key)
                seen_topics.add(key)
        in_tokens  += int(r.get("input_tokens",  0) or 0)
        out_tokens += int(r.get("output_tokens", 0) or 0)
        if r.get("language"):
            languages.append(r["language"])
        bilingual_flags.append(bool(r.get("is_bilingual", False)))
        sec = float(r.get("gemini_seconds", 0.0) or 0.0)
        gemini_total += sec
        chunk_seconds.append(round(sec, 2))
        successful_chunks += 1

    merged_lang = languages[0] if languages else fallback_language
    is_bilingual = any(bilingual_flags)
    # Carry forward the translation_language from any successful chunk (all
    # chunks are called with the same value, so picking the first one is fine).
    translation_lang = next(
        (r.get("translation_language") for r, _, _ in results if r.get("translation_language")),
        None,
    )
    text_md = _flatten_segments_to_markdown(merged_segments, is_bilingual)

    out: dict = {
        "language":      merged_lang,
        "is_bilingual":  is_bilingual,
        "key_topics":    merged_topics,
        "segments":      merged_segments,
        "summary":       _empty_summary(),   # downstream stage runs on merged segments
        "text":          text_md,
        "input_tokens":  in_tokens,
        "output_tokens": out_tokens,
        "gemini_seconds": round(gemini_total, 2),
        "chunk_count":    successful_chunks,
        "chunk_seconds":  chunk_seconds,
    }
    if translation_lang is not None:
        out["translation_language"] = translation_lang
        out["translation_label"]    = translation_display_label(translation_lang)
    return out


def gemini_batch_transcribe_smart(
    audio_path: str,
    language: str = "zh",
    note_id: str = "",
    translation_language: str = "en",
) -> dict:
    """Drop-in replacement for gemini_batch_transcribe that handles audio of
    arbitrary length and format.

    Pass `translation_language` through to each chunk's Gemini call so the
    user-selected translation target is honored end-to-end.

    Pipeline:
      1. ffmpeg-normalize input to mono 16 kHz Opus, bitrate per duration.
      2. If duration <= _TARGET_CHUNK_MIN, single inline transcribe call.
      3. Otherwise: ffmpeg silencedetect -> split at silence-snapped points
         into N chunks each <= _TARGET_CHUNK_MIN minutes; transcribe each;
         merge segments/key_topics, offset-correct timestamps, sum tokens.

    Returns the same shape as gemini_batch_transcribe so callers don't change.
    Temp files (normalized + chunks) live in a TemporaryDirectory and are
    auto-cleaned. The original audio_path is left untouched.
    """
    src = Path(audio_path)
    if not src.exists():
        return {
            "error": f"audio file not found: {audio_path}",
            "language": language, "is_bilingual": False, "key_topics": [],
            "segments": [], "summary": _empty_summary(), "text": "",
            "input_tokens": 0, "output_tokens": 0,
            "gemini_seconds": 0.0, "total_seconds": 0.0,
            "coverage_gaps": [],
        }

    t_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="alphagraph_polish_") as td:
        work = Path(td)
        try:
            normalized, duration = _normalize_audio_for_meeting(src, work)
        except Exception:
            logger.exception("normalize step failed; falling back to original file")
            result = gemini_batch_transcribe(audio_path, language, note_id, translation_language)
            result["total_seconds"] = round(time.perf_counter() - t_start, 2)
            result["audio_duration_sec"] = round(_ffprobe_duration(src), 1)
            result["coverage_gaps"] = _detect_coverage_gaps(
                result.get("segments", []), result["audio_duration_sec"],
            )
            return result

        # Threshold logic: only split if audio is meaningfully longer than
        # _SPLIT_THRESHOLD_MIN. Splitting a 31-min audio into two 15-min
        # chunks would just add API overhead without much reliability win.
        if duration <= _SPLIT_THRESHOLD_MIN * 60:
            result = gemini_batch_transcribe(str(normalized), language, note_id, translation_language)
            result["chunk_count"] = 1
            result["total_seconds"] = round(time.perf_counter() - t_start, 2)
            result["audio_duration_sec"] = round(duration, 1)
            result["coverage_gaps"] = _detect_coverage_gaps(
                result.get("segments", []), duration,
            )
            logger.info(
                "transcribe_smart: single-chunk done duration=%.0fs gemini=%.1fs total=%.1fs gaps=%d",
                duration, result.get("gemini_seconds", 0.0), result["total_seconds"],
                len(result["coverage_gaps"]),
            )
            return result

        # Long meeting -- split into ~_TARGET_CHUNK_MIN-min chunks.
        n_chunks = max(2, ceil(duration / (_TARGET_CHUNK_MIN * 60)))
        logger.info(
            "transcribe_smart: planning split duration=%.0fs (%.1fmin) -> %d chunks of ~%.1fmin each",
            duration, duration / 60, n_chunks, duration / n_chunks / 60,
        )
        try:
            split_points = _find_silence_split_points(normalized, n_chunks)
            chunks = _split_audio_at(normalized, split_points, work)
        except Exception:
            logger.exception("split step failed; falling back to single inline call (may exceed inline limit)")
            result = gemini_batch_transcribe(str(normalized), language, note_id, translation_language)
            result["chunk_count"] = 1
            result["total_seconds"] = round(time.perf_counter() - t_start, 2)
            result["audio_duration_sec"] = round(duration, 1)
            result["coverage_gaps"] = _detect_coverage_gaps(
                result.get("segments", []), duration,
            )
            return result

        # Run all chunks IN PARALLEL via a thread pool. gemini_batch_transcribe
        # is a blocking call (requests.post on Gemini), so threads work fine
        # despite the GIL -- the GIL is released during I/O. Cap workers at 6
        # so a 4-hour audio (~9 chunks) doesn't open 9 simultaneous Gemini
        # connections in case Google's per-key concurrency limit kicks in.
        # Wall time drops from sum(chunks) to max(chunks) -- typically ~50%
        # for the 81-min test file.
        for idx, (chunk_path, start, end) in enumerate(chunks, start=1):
            logger.info(
                "queued chunk %d/%d (%.0fs - %.0fs = %.1fmin, %.1fMB)",
                idx, len(chunks), start, end, (end - start) / 60,
                chunk_path.stat().st_size / 1024 / 1024,
            )
        chunk_results: list[tuple[dict, float, float]] = []
        with ThreadPoolExecutor(max_workers=min(len(chunks), 6)) as ex:
            futures = {
                ex.submit(gemini_batch_transcribe, str(p), language, note_id, translation_language): (p, s, e)
                for (p, s, e) in chunks
            }
            for fut in as_completed(futures):
                p, s, e = futures[fut]
                try:
                    r = fut.result()
                except Exception as exc:
                    logger.exception("chunk transcribe raised at %.0fs: %s", s, exc)
                    r = {
                        "error":          f"chunk transcribe raised: {exc}",
                        "language":       language,
                        "is_bilingual":   False,
                        "key_topics":     [],
                        "segments":       [],
                        "summary":        _empty_summary(),
                        "text":           "",
                        "input_tokens":   0,
                        "output_tokens":  0,
                        "gemini_seconds": 0.0,
                    }
                chunk_results.append((r, s, e))
        # Re-sort by chunk start so merge ordering is deterministic
        # regardless of which Gemini call returned first.
        chunk_results.sort(key=lambda x: x[1])

        merged = _merge_chunk_results(chunk_results, language)
        merged["total_seconds"] = round(time.perf_counter() - t_start, 2)
        merged["audio_duration_sec"] = round(duration, 1)
        merged["coverage_gaps"] = _detect_coverage_gaps(
            merged.get("segments", []), duration,
        )
        logger.info(
            "transcribe_smart: %d-chunk done duration=%.0fs gemini=%.1fs total=%.1fs chunks=%s gaps=%d",
            merged.get("chunk_count", 0), duration,
            merged.get("gemini_seconds", 0.0), merged["total_seconds"],
            merged.get("chunk_seconds", []), len(merged["coverage_gaps"]),
        )
        if merged["coverage_gaps"]:
            for g in merged["coverage_gaps"]:
                logger.warning(
                    "  COVERAGE GAP %s: %.0fs -> %.0fs (%.1fmin missing)",
                    g["kind"], g["start_sec"], g["end_sec"], g["duration_sec"] / 60,
                )
        return merged


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


# ----- Translation target codes & labels ------------------------------------
# `translation_language` controls what goes into segments[*].text_english.
# (The field name stays "text_english" for backwards-compat; the meta carries
# the actual language code so the UI can render the right column header.)
#
#   "none"      -> skip translation entirely; is_bilingual = false
#   "en"        -> English (current default behavior)
#   "zh-hans"   -> Simplified Chinese
#   "zh-hant"   -> Traditional Chinese
#   "ja"        -> Japanese
#   "ko"        -> Korean
_TRANSLATION_LABELS: dict[str, str] = {
    "none":    "(none)",
    "en":      "English",
    "zh-hans": "Simplified Chinese (简体中文)",
    "zh-hant": "Traditional Chinese (繁體中文)",
    "ja":      "Japanese (日本語)",
    "ko":      "Korean (한국어)",
}

# Compact display labels for UI table headers (no parenthesized translation
# of the language's own name — that's noisy in a 3-col table header).
_TRANSLATION_DISPLAY: dict[str, str] = {
    "en":      "English",
    "zh-hans": "简体中文",
    "zh-hant": "繁體中文",
    "ja":      "日本語",
    "ko":      "한국어",
}


def translation_display_label(code: str) -> str:
    """Resolve a translation_language code to a short header-friendly label.

    Known codes -> compact native-script label (e.g. zh-hans -> "简体中文").
    Free-form strings (e.g. "Arabic", "Vietnamese") -> the string itself,
    sanitized to <=40 chars for header rendering.
    """
    if not code or code == "none":
        return ""
    if code in _TRANSLATION_DISPLAY:
        return _TRANSLATION_DISPLAY[code]
    cleaned = re.sub(r"[\r\n\"'`]", " ", code).strip()
    return cleaned[:40] or "English"


def gemini_batch_transcribe(
    audio_path: str,
    language: str = "zh",
    note_id: str = "",
    translation_language: str = "en",
) -> dict:
    """
    Run Gemini V2-quality batch transcription on the full audio file.

    `translation_language` controls what goes into text_english: one of the
    keys of _TRANSLATION_LABELS. "none" skips translation entirely.

    Returns a structured dict:
      {
        "language": str,           # detected language code
        "is_bilingual": bool,      # True for zh/ja/ko source (English translation provided)
        "key_topics": list[str],
        "segments": [              # one entry per spoken segment
          {"timestamp": "MM:SS", "speaker": str,
           "text_original": str, "text_english": str},
          ...
        ],
        "summary": dict,           # detailed MeetingSummary structure (see _empty_summary)
        "text": str,               # flattened markdown form (for export/backup)
        "input_tokens": int,
        "output_tokens": int,
        "error": str (optional),
      }
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "error": "GEMINI_API_KEY not set",
            "language": language,
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "summary": _empty_summary(),
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    vocab_context = load_vocabulary(language)

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    ext = Path(audio_path).suffix.lower()
    mime_types = {".opus": "audio/ogg", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
    mime = mime_types.get(ext, "audio/ogg")

    lang_names = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}
    lang_name = lang_names.get(language, "Chinese")

    # When primary language is Chinese, force Simplified output. Gemini
    # otherwise picks Traditional or Simplified based on audio cues. The
    # zh-Hans/zh-Hant toggle on the note view lets the user switch later.
    chinese_variant_rule = ""
    if language == "zh":
        chinese_variant_rule = (
            "\n9. Chinese script: write ALL Chinese text in **Simplified Chinese** (简体字), "
            "not Traditional. Convert any Traditional characters you'd otherwise emit "
            "to their Simplified equivalents — for example "
            "臺灣→台湾, 內地→内地, 雲端→云端, 開發→开发, 業務→业务, 這個→这个, 發展→发展, "
            "說→说, 這→这, 麼→么, 個→个, 業→业, 開→开. Apply consistently to "
            "text_original, key_topics, speaker labels, and any other Chinese fields."
        )

    # Translation behavior is driven by `translation_language`. Three cases:
    #   "none"                          -> monolingual; text_english = ""
    #   one of _TRANSLATION_LABELS keys -> use the friendly label
    #   anything else (free-form text)  -> use the string itself as the target
    #                                       language name (e.g. "French",
    #                                       "Arabic", "Vietnamese", "Thai",
    #                                       "Persian", "Swahili", ...)
    # Free-form is sanitized to <=80 chars and stripped of newlines / quotes
    # to keep the prompt clean.
    if translation_language == "none":
        translation_rule = (
            "Set `text_english` to an empty string for every segment, "
            "and set `is_bilingual` to false. We do not want a translation."
        )
        bilingual_default = "false"
    else:
        if translation_language in _TRANSLATION_LABELS:
            target_label = _TRANSLATION_LABELS[translation_language]
        else:
            # Free-form user input -- sanitize.
            cleaned = re.sub(r"[\r\n\"'`]", " ", translation_language)
            target_label = cleaned.strip()[:80] or "English"
        translation_rule = (
            f"Translate every segment into **{target_label}** and put the translation "
            f"in `text_english` (the field is legacy-named -- it just means 'translation'). "
            f"For English-only source audio, set `text_english` equal to `text_original` "
            f"only when the requested target IS English; otherwise translate as requested. "
            f"Use natural fluent {target_label} -- not a literal word-for-word rendering."
        )
        bilingual_default = "true"

    prompt = f"""{vocab_context}
Transcribe this financial meeting audio. Primary language: {lang_name} with English code-switching.

Return ONLY valid JSON matching this exact schema:
{{
  "language": "{language}",
  "is_bilingual": {bilingual_default},
  "key_topics": ["topic1", "topic2", ...],
  "segments": [
    {{
      "timestamp": "MM:SS",
      "speaker": "speaker name or role (e.g. 'Tanaka (CFO)')",
      "text_original": "exact transcription in the meeting's primary language",
      "text_english": "translation OR empty string -- see rule 2"
    }}
  ]
}}

Rules:
1. Timestamps in MM:SS format relative to the start of the audio.
2. {translation_rule}
3. NEVER repeat a segment. If audio is unclear, emit a single segment with text_original="[audio unclear]".
4. Preserve financial terminology and proper nouns exactly as spoken.
5. key_topics: 5-10 short strings capturing the main topics discussed.
6. Do NOT include any summary / key_points / numbers / financial_metrics output — the downstream stage handles that separately from the transcript text.
7. CRITICAL — keep JSON well-formed: if approaching the token budget, cut the transcript short rather than truncating mid-value. A short, complete JSON beats a long, truncated one.
8. is_bilingual reflects whether you produced a translation: true when text_english differs from text_original, false otherwise.{chinese_variant_rule}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    _t0 = time.perf_counter()
    resp = requests.post(
        url,
        json={
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": audio_b64}},
            ]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 65536,
                "responseMimeType": "application/json",
            },
        },
        timeout=3600,  # 60 min — covers long earnings calls and podcasts (Q3 from plan)
    )
    gemini_seconds = round(time.perf_counter() - _t0, 2)

    if resp.status_code != 200:
        logger.warning("gemini_batch_transcribe: status=%s after %.1fs (file=%s, lang=%s)",
                       resp.status_code, gemini_seconds, Path(audio_path).name, language)
        return {
            "error": f"Gemini API error: {resp.status_code}",
            "language": language,
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "summary": _empty_summary(),
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "gemini_seconds": gemini_seconds,
        }

    result = resp.json()
    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
    usage = result.get("usageMetadata", {})

    parsed = _parse_polish_response(raw_text)
    # Fill in the fallback markdown if parsing failed so downstream still has *something* to show.
    text_md = _flatten_segments_to_markdown(parsed["segments"], parsed["is_bilingual"]) \
        if parsed["segments"] else parsed.get("text_markdown_fallback", "")

    audio_size_mb = round(os.path.getsize(audio_path) / 1024 / 1024, 2)
    logger.info("gemini_batch_transcribe done file=%s size=%.2fMB lang=%s xlate=%s segments=%d gemini=%.1fs in_tok=%d out_tok=%d",
                Path(audio_path).name, audio_size_mb, language, translation_language,
                len(parsed["segments"]), gemini_seconds,
                usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0))

    # If user requested no translation, force is_bilingual=false and clear
    # text_english as a defensive measure (in case Gemini ignored the rule).
    is_bilingual = parsed["is_bilingual"]
    if translation_language == "none":
        is_bilingual = False
        for seg in parsed["segments"]:
            seg["text_english"] = ""

    return {
        "language": parsed["language"] or language,
        "is_bilingual": is_bilingual,
        "translation_language": translation_language,
        "translation_label": translation_display_label(translation_language),
        "key_topics": parsed["key_topics"],
        "segments": parsed["segments"],
        "summary": parsed["summary"],
        "text": text_md,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
        "gemini_seconds": gemini_seconds,
        "audio_size_mb": audio_size_mb,
    }


def _parse_polish_response(raw_text: str) -> dict:
    """
    Parse Gemini's structured-output response. Returns a dict with keys:
    `language`, `is_bilingual`, `key_topics`, `segments`, `summary`, and
    optionally `text_markdown_fallback` when we couldn't parse JSON even after
    repair.

    Handles two common Gemini failure modes:
      1. Valid JSON — fast path via `json.loads`.
      2. Truncated JSON (output hit `maxOutputTokens`) — `json_repair` closes
         dangling strings/arrays/objects and returns best-effort parse. Also
         strips trailing repetition loops before repairing.
    """
    import json as _json

    # Fast path: strict parse.
    try:
        data = _json.loads(raw_text)
    except (ValueError, TypeError):
        data = _repair_and_parse(raw_text)

    if not isinstance(data, dict):
        return {
            "language": "",
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "summary": _empty_summary(),
            "text_markdown_fallback": raw_text,
        }

    segments = [
        {
            "timestamp": str(s.get("timestamp", "")),
            "speaker": str(s.get("speaker", "")),
            # Strip in-segment repetition loops (Gemini sometimes falls into
            # `我，我，我，...` or `50%, 50%, 50%, ...` inside a single text
            # field). Without this the bad segment renders as a 2,000-character
            # wall of repeated tokens that breaks the editor table.
            "text_original": _strip_intra_text_loop(str(s.get("text_original", ""))),
            "text_english":  _strip_intra_text_loop(str(s.get("text_english",  ""))),
        }
        for s in (data.get("segments") or [])
        if isinstance(s, dict)
    ]
    # Anti-repetition pass on the assembled segments (kept here rather than
    # in the prompt because Gemini sometimes produces duplicates anyway).
    # Drop segments whose text becomes empty after the loop strip.
    deduped: list[dict] = []
    for seg in segments:
        if not seg["text_original"].strip() and not seg["text_english"].strip():
            continue
        if deduped and seg["text_original"] == deduped[-1]["text_original"]:
            continue
        deduped.append(seg)

    return {
        "language": str(data.get("language", "")),
        "is_bilingual": bool(data.get("is_bilingual", False)),
        "key_topics": [str(t) for t in (data.get("key_topics") or []) if t],
        "segments": deduped,
        "summary": _parse_summary(data.get("summary") or {}),
    }


def _repair_and_parse(raw_text: str):
    """Best-effort recovery when strict JSON parsing fails. Uses the
    `json_repair` library, which handles common cases:
      - truncated output (dangling strings, arrays, objects)
      - trailing commas
      - unquoted keys

    Also strips trailing repetition loops before repairing, because Gemini
    occasionally spirals on a short phrase (`"50%", "50%", ...`) and the
    repair library treats each repetition as valid data.
    """
    try:
        import json_repair
    except ImportError:
        return None

    # Strip trailing repetition loops: if the last ~40 non-whitespace tokens
    # are the same quoted string repeated, chop them back to a single copy.
    cleaned = _strip_repetition_loop(raw_text)

    try:
        return json_repair.loads(cleaned)
    except Exception:
        return None


def _strip_intra_text_loop(text: str, *, min_repeats: int = 5) -> str:
    """Collapse repetition loops inside a single transcript-segment text field.

    Gemini occasionally falls into a degenerate state where it emits the same
    1-30-character unit hundreds of times in one `text_original` (the famous
    `我，我，我，...` loop). The whole-response `_strip_repetition_loop` only
    runs on JSON-repair errors -- when the JSON parses cleanly but a SINGLE
    text value is full of repetition, that helper never fires.

    Strategy: walk left-to-right finding any substring whose immediate
    repetition count >= `min_repeats`, then collapse it to a single copy
    followed by `...`. We only check units of 1-30 chars to avoid pathological
    backtracking on legitimate long-but-similar prose.

    Examples:
      "我，我，我，我，我，我，我，我，我，"  -> "我，..."
      "50%, 50%, 50%, 50%, 50%, 50%"       -> "50%..."
      "Hello world. Hello world. Hello world. Hello world. Hello world."
                                            -> "Hello world. ..."
      "Apple Apple Apple"                    (only 3 reps) -> unchanged
    """
    if not text or len(text) < 16:
        return text
    import re as _re
    # Greedy: longest possible unit first so we don't match substrings of a
    # longer repeating motif. Cap unit length at 30 chars so we don't spend
    # exponential time on Chinese-paragraph-like inputs.
    for unit_len in range(30, 0, -1):
        # `(?P<u>.{unit_len}?)` non-greedy unit, then min_repeats-1 immediate
        # backreferences. The {min_repeats-1} extras + the original = >= min_repeats.
        pattern = _re.compile(
            rf"(?P<u>.{{{unit_len}}}?)(?:(?P=u)){{{min_repeats - 1},}}",
            _re.DOTALL,
        )
        text = pattern.sub(lambda m: m.group("u") + "...", text)
    return text


def _strip_repetition_loop(raw: str) -> str:
    """Heuristic: if the tail of the response is a quoted string repeated
    at least 4 times (e.g. `"50%", "50%", "50%", "50%"`), collapse the tail
    back to a single copy. Prevents the repair step from preserving the loop."""
    import re as _re
    # Match 4+ consecutive identical quoted strings (possibly with trailing comma/whitespace).
    pattern = _re.compile(r'("([^"\\]|\\.)*?")(\s*,\s*\1){3,}')
    while True:
        m = pattern.search(raw)
        if not m:
            break
        # Replace the whole matched loop with a single copy of the string.
        raw = raw[: m.start()] + m.group(1) + raw[m.end():]
    return raw


def _empty_summary() -> dict:
    """Return a fully-populated empty MeetingSummary structure so frontend code
    never has to guard against missing keys."""
    return {
        "storyline": "",
        "key_points": [],
        "all_numbers": [],
        "recent_updates": [],
        "financial_metrics": {"revenue": [], "profit": [], "orders": []},
    }


def _dedupe_preserving_order(items: list) -> list:
    """Remove duplicate strings while preserving first-seen order. Protects
    against Gemini's occasional repetition loops in the list-valued summary
    fields (all_numbers, recent_updates, financial_metrics.*)."""
    seen = set()
    out = []
    for x in items:
        key = str(x)
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def _normalize_number_mention(raw) -> dict:
    """Coerce one all_numbers entry into the {label, value, quote} shape.
    Accepts either the new structured dict or the legacy plain string for
    backwards compatibility with notes written before the refactor."""
    if isinstance(raw, dict):
        return {
            "label": str(raw.get("label", "") or ""),
            "value": str(raw.get("value", "") or ""),
            "quote": str(raw.get("quote", "") or ""),
        }
    if isinstance(raw, str):
        # Legacy format — no label/quote context; promote the bare value.
        return {"label": "", "value": raw, "quote": ""}
    return {"label": "", "value": "", "quote": ""}


def _dedupe_number_mentions(items: list) -> list:
    """Dedupe NumberMention dicts by (value, label) pair. Preserves first-seen
    order. Empty {label:'', value:'', quote:''} entries are dropped."""
    seen = set()
    out = []
    for m in items:
        key = (m.get("value", ""), m.get("label", ""))
        if key == ("", ""):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def _parse_summary(raw: dict) -> dict:
    """Parse and sanitise the `summary` sub-object from a Gemini response.
    Always returns a complete MeetingSummary shape — missing fields default
    to empty lists / strings. All list fields are de-duplicated (Gemini
    occasionally emits the same value 10+ times in a repetition spiral)."""
    if not isinstance(raw, dict):
        return _empty_summary()

    key_points = []
    for kp in (raw.get("key_points") or []):
        if not isinstance(kp, dict):
            continue
        sub_points = []
        for sp in (kp.get("sub_points") or []):
            if not isinstance(sp, dict):
                continue
            sub_points.append({
                "text": str(sp.get("text", "")),
                "supporting": str(sp.get("supporting", "")),
            })
        key_points.append({
            "title": str(kp.get("title", "")),
            "sub_points": sub_points,
        })

    fm_raw = raw.get("financial_metrics") or {}
    if not isinstance(fm_raw, dict):
        fm_raw = {}
    financial_metrics = {
        "revenue": _dedupe_preserving_order([str(x) for x in (fm_raw.get("revenue") or []) if x]),
        "profit":  _dedupe_preserving_order([str(x) for x in (fm_raw.get("profit")  or []) if x]),
        "orders":  _dedupe_preserving_order([str(x) for x in (fm_raw.get("orders")  or []) if x]),
    }

    # all_numbers: new {label, value, quote} schema. Legacy string entries get
    # coerced so old notes still render sensibly.
    numbers = [
        _normalize_number_mention(n)
        for n in (raw.get("all_numbers") or [])
        if n
    ]

    return {
        "storyline": str(raw.get("storyline", "")),
        "key_points": key_points,
        "all_numbers": _dedupe_number_mentions(numbers),
        "recent_updates": _dedupe_preserving_order([str(u) for u in (raw.get("recent_updates") or []) if u]),
        "financial_metrics": financial_metrics,
    }


def _flatten_segments_to_markdown(segments: list, is_bilingual: bool) -> str:
    """
    Render segments as a markdown table. Two columns for monolingual
    (Time | Text) or three for bilingual (Time | Original | English).
    Used for export / backup; the frontend builds its own TipTap table
    directly from the structured segments, not this markdown.
    """
    if not segments:
        return ""

    if is_bilingual:
        lines = ["| Time | 原文 | English |", "|------|------|---------|"]
        for s in segments:
            ts = s.get("timestamp", "")
            orig = (s.get("text_original", "") or "").replace("|", "\\|").replace("\n", " ")
            eng = (s.get("text_english", "") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {ts} | {orig} | {eng} |")
        return "\n".join(lines)
    else:
        lines = ["| Time | Text |", "|------|------|"]
        for s in segments:
            ts = s.get("timestamp", "")
            txt = (s.get("text_original", "") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {ts} | {txt} |")
        return "\n".join(lines)


def gemini_generate_summary(
    segments: list,
    language_hint: str = "en",
    note_id: str = "",
) -> dict:
    """
    Produce ONLY the MeetingSummary from a list of already-transcribed
    segments. No audio processing — this is a cheap text-only Gemini call
    (~$0.001-0.01 vs ~$0.05-0.20 for an audio pass) that can be re-run freely
    when the summary prompt is improved.

    Input: segments in the standard shape used by the rest of the pipeline:
    {timestamp, speaker, text_original, text_english}.

    Output: dict with keys:
      {
        "summary": MeetingSummary shape,
        "input_tokens": int,
        "output_tokens": int,
        "error": str (optional),
      }
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "error": "GEMINI_API_KEY not set",
            "summary": _empty_summary(),
            "input_tokens": 0,
            "output_tokens": 0,
        }

    lang_names = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}
    lang_name = lang_names.get(language_hint, "English")

    # Format the segments as a plain-text transcript Gemini can reason over.
    # Prefer English translation when present so non-EN meetings still produce
    # an English summary grounded in specific phrasing.
    transcript_lines = []
    for s in segments:
        ts = s.get("timestamp", "")
        original = (s.get("text_original") or "").strip()
        english = (s.get("text_english") or "").strip()
        if english and english != original:
            line = f"[{ts}] {english}" if ts else english
        elif original:
            line = f"[{ts}] {original}" if ts else original
        else:
            continue
        transcript_lines.append(line)
    transcript_text = "\n".join(transcript_lines)
    if not transcript_text.strip():
        return {
            "error": "No transcript content to summarise.",
            "summary": _empty_summary(),
            "input_tokens": 0,
            "output_tokens": 0,
        }

    vocab_context = load_vocabulary(language_hint)

    prompt = f"""{vocab_context}
You are an expert financial-analyst assistant. Produce a detailed
analyst-grade summary of the following meeting / interview / conference-call
transcript. Primary source language: {lang_name}.

TRANSCRIPT (timestamps in brackets):
{transcript_text[:60000]}

Return ONLY valid JSON matching this exact schema:
{{
  "storyline": "1-2 paragraph narrative of how the meeting flowed, tying together the main arc in English",
  "key_points": [
    {{
      "title": "short title (3-8 words)",
      "sub_points": [
        {{
          "text": "the sub-point itself, one sentence",
          "supporting": "2-3 sentence supporting argument grounded in what was said. Quote specific numbers or claims where possible."
        }}
      ]
    }}
  ],
  "all_numbers": [
    {{
      "label": "short description of what the number refers to (e.g., 'Stargate datacenter capacity', 'Q1 revenue', 'ARM partnership value')",
      "value": "the number with units (e.g., '1.2 gigawatt', '$2.1B', '20% YoY')",
      "quote": "the exact verbatim sentence from the transcript containing this number"
    }}
  ],
  "recent_updates": ["recent events / launches / partnerships / personnel changes / acquisitions mentioned as having happened recently. One item per string."],
  "financial_metrics": {{
    "revenue": ["revenue-related mentions, one per string. Example: 'Q1 revenue $2.1B, up 20% YoY'"],
    "profit": ["profit / margin / operating income mentions"],
    "orders": ["backlog / order book / bookings mentions"]
  }}
}}

Rules:
1. Summary fields are all in English regardless of source language.
2. all_numbers: include every meaningful numeric value mentioned. Each entry
   MUST populate all three fields (label, value, quote). The quote must be
   VERBATIM from the transcript — do not paraphrase. If a number appears
   multiple times for the same concept, include it once. Aim for ~10-60
   entries depending on content density.
3. NEVER fabricate numbers or quotes that weren't in the input. Every claim
   in every field must be traceable to the transcript.
4. Preserve financial terminology and proper nouns exactly as spoken.
5. If the transcript is short / light on content, still produce storyline +
   key_points with what's there. Empty lists for all_numbers / financial_metrics
   / recent_updates are acceptable.
6. CRITICAL — no repetition loops: each all_numbers entry must be unique
   (different label OR different value). recent_updates entries must be
   unique strings. Dedupe before returning.
7. CRITICAL — keep JSON well-formed. If approaching the token budget, CUT
   the lists short (fewer entries) rather than truncating mid-value. A
   shorter complete JSON beats a longer truncated one.
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    resp = requests.post(
        url,
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 65536,
                "responseMimeType": "application/json",
            },
        },
        timeout=600,
    )

    if resp.status_code != 200:
        return {
            "error": f"Gemini API error: {resp.status_code}",
            "summary": _empty_summary(),
            "input_tokens": 0,
            "output_tokens": 0,
        }

    result = resp.json()
    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
    usage = result.get("usageMetadata", {})

    # Re-use the hardened parser — it knows how to repair truncated JSON and
    # dedupe loops. We wrap the raw summary body so it parses through the same
    # path as the old polish response.
    import json as _json
    try:
        data = _json.loads(raw_text)
    except (ValueError, TypeError):
        data = _repair_and_parse(raw_text) or {}

    summary = _parse_summary(data if isinstance(data, dict) else {})

    return {
        "summary": summary,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
    }
