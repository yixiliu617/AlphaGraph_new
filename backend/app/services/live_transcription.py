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

import requests

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


def gemini_batch_transcribe(
    audio_path: str,
    language: str = "zh",
    note_id: str = "",
) -> dict:
    """
    Run Gemini V2-quality batch transcription on the full audio file.

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

    prompt = f"""{vocab_context}
Transcribe this financial meeting audio. Primary language: {lang_name} with English code-switching.

Return ONLY valid JSON matching this exact schema:
{{
  "language": "{language}",
  "is_bilingual": true,
  "key_topics": ["topic1", "topic2", ...],
  "segments": [
    {{
      "timestamp": "MM:SS",
      "speaker": "speaker name or role (e.g. 'Tanaka (CFO)')",
      "text_original": "exact transcription in the meeting's primary language",
      "text_english": "English translation of this segment"
    }}
  ]
}}

Rules:
1. Timestamps in MM:SS format relative to the start of the audio.
2. Provide `text_english` for every segment. For English-only meetings, set `text_english` equal to `text_original`.
3. For English-only meetings, set `is_bilingual` to false.
4. NEVER repeat a segment. If audio is unclear, emit a single segment with text_original="[audio unclear]".
5. Preserve financial terminology and proper nouns exactly as spoken.
6. key_topics: 5-10 short strings capturing the main topics discussed.
7. Do NOT include any summary / key_points / numbers / financial_metrics output — the downstream stage handles that separately from the transcript text.
8. CRITICAL — keep JSON well-formed: if approaching the token budget, cut the transcript short rather than truncating mid-value. A short, complete JSON beats a long, truncated one."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

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

    if resp.status_code != 200:
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
        }

    result = resp.json()
    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
    usage = result.get("usageMetadata", {})

    parsed = _parse_polish_response(raw_text)
    # Fill in the fallback markdown if parsing failed so downstream still has *something* to show.
    text_md = _flatten_segments_to_markdown(parsed["segments"], parsed["is_bilingual"]) \
        if parsed["segments"] else parsed.get("text_markdown_fallback", "")

    return {
        "language": parsed["language"] or language,
        "is_bilingual": parsed["is_bilingual"],
        "key_topics": parsed["key_topics"],
        "segments": parsed["segments"],
        "summary": parsed["summary"],
        "text": text_md,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
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
            "text_original": str(s.get("text_original", "")),
            "text_english": str(s.get("text_english", "")),
        }
        for s in (data.get("segments") or [])
        if isinstance(s, dict)
    ]
    # Anti-repetition pass on the assembled segments (kept here rather than
    # in the prompt because Gemini sometimes produces duplicates anyway).
    deduped: list[dict] = []
    for seg in segments:
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
