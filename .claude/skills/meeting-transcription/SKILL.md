---
name: meeting-transcription
description: Generating high-quality multilingual meeting transcripts from audio using Gemini native audio + vocabulary context. Covers EN/ZH/JA/KO code-switching, anti-repetition, domain terms, and clickable timestamps.
---

# Meeting Transcription Skill

## Overview

Transcribe financial meetings from audio files using Gemini 2.5 Flash native audio input. Supports Chinese, English, Japanese, Korean, and code-switching (mixed language in same sentence). Optimized for sell-side analyst calls, earnings calls, and investor meetings.

## Architecture — Two Modes

### Cloud Mode (Best Quality — Default)
```
Audio file (OPUS/WAV/MP3)
    → Gemini 2.5 Flash native audio API (single call)
    → Transcript with speakers, timestamps, bold key points, topic summary
    → Post-processing: repetition loop cleanup
    → ~50-70s for 30-min meeting, ~$0.04-0.06
```

### Local Mode (Private — Audio Stays On-Premise)
```
Audio file
    → SenseVoice-Small (local CPU/GPU, 234M params)
    → Dictionary corrections (meeting_vocabulary.json)
    → Gemini LLM polish (text only — no audio sent)
    → ~308s for 30-min meeting, ~$0.02
```

## Gemini Native Audio — The Prompt

### Critical Elements (in order of importance)

**1. Vocabulary Context (MOST IMPORTANT)**

Load `tools/audio_recorder/meeting_vocabulary.json` and prepend company names, aliases, and critical distinctions to the prompt. This is the single highest-leverage improvement.

```
VOCABULARY:
- 阿里巴巴 (Alibaba, BABA/9988.HK) NOTE: 巴巴/88/八八 = Alibaba the company, NOT the number
- 满帮 (Full Truck Alliance/Manbang, YMM) NOTE: 司机匹配平台，NOT 美团
- 金山云 (Kingsoft Cloud, KC/3896.HK) — NOT 京象雲
- 通义千问/Qwen (Alibaba's AI model) — NOT 钱问
CRITICAL: 巴巴/88 = Alibaba. 满帮(YMM) != 美团. 千问 = Qwen.
```

Without this, Gemini will:
- Confuse 满帮 (freight) with 美团 (food delivery) — similar-sounding
- Transcribe 巴巴 as "88" (the number)
- Write 金山云 as 京象雲
- Write 千问 as 钱问

**2. Anti-Repetition Instruction (CRITICAL)**

```
CRITICAL ANTI-REPETITION RULE: NEVER repeat the same phrase more than once.
If audio is unclear or has static/noise, write [audio unclear] and move to
the next clear segment. Do NOT loop or repeat text under any circumstances.
```

Without this, Gemini WILL get stuck in repetition loops on unclear audio sections, generating thousands of characters of repeated phrases like "它在2季度的话" × 100.

**3. Temperature Setting**

```python
"generationConfig": {
    "temperature": 0.2,  # NOT 0.1 — too low causes repetition loops
    "maxOutputTokens": 65536,
}
```

Temperature 0.1 is too deterministic for long transcriptions and causes loops. 0.2 is the sweet spot.

**4. Post-Processing Safety Net**

ALWAYS run this regex after receiving the transcript:
```python
import re
cleaned = re.sub(r'(.{10,50}?)\1{3,}', r'\1', text)
```
This catches any remaining repetition loops that slipped through the prompt instruction.

### Full Prompt Template

```python
prompt = f"""{vocabulary_context}

Transcribe this {duration}-minute {language} financial meeting with English code-switching.

Rules:
1. Exact transcription with speaker names + timestamps [MM:SS]
2. Bold **key data points** (numbers, percentages, stock recommendations)
3. Key topics list at top
4. CRITICAL ANTI-REPETITION RULE: NEVER repeat the same phrase more than once.
   If audio is unclear, write [audio unclear] and skip ahead.
"""
```

### API Call

```python
import base64, requests

with open(audio_path, "rb") as f:
    audio_b64 = base64.b64encode(f.read()).decode()

resp = requests.post(
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
    json={
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}},
        ]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 65536},
    },
    timeout=900,
)
```

**Audio format:** OPUS (audio/ogg) is preferred — smallest file size. WAV, MP3, M4A also work.
**Size limit:** ~20MB inline base64. For larger files, use Gemini File API.
**Timeout:** Set to 900s (15 min) — long meetings can take 60-120s to process.

## Vocabulary File — `meeting_vocabulary.json`

This is a JSON file containing domain-specific terms that get prepended to every transcription prompt. Structure:

```json
{
  "companies": {
    "sector_name": [
      {
        "zh": "阿里巴巴",
        "en": "Alibaba",
        "ticker": "BABA/9988.HK",
        "aliases": ["巴巴", "88", "八八"],
        "products": ["Temu", "通义千问/Qwen", "阿里云"],
        "notes": "When analysts say '巴巴' or '88' they mean Alibaba"
      }
    ]
  },
  "common_misrecognitions": [
    {"wrong": "美团 (when discussing freight)", "correct": "满帮 (YMM)"},
    {"wrong": "钱问", "correct": "千问/Qwen (Alibaba AI model)"},
    {"wrong": "88 (when referring to a stock)", "correct": "巴巴/阿里巴巴 (Alibaba)"}
  ]
}
```

**How to maintain:** When you find a transcription error, add it to `common_misrecognitions`. When covering a new sector, add companies to the appropriate section. The file grows over time and future transcriptions automatically improve.

**Location:** `tools/audio_recorder/meeting_vocabulary.json`

## Timestamp Format

Gemini outputs timestamps in `MM:SS:cs` format for meetings under 1 hour (minutes:seconds:centiseconds), NOT `HH:MM:SS`.

- `[04:52:30]` = 4 minutes 52 seconds (NOT 4 hours 52 minutes)
- `[22:15:00]` = 22 minutes 15 seconds

**Parsing logic:**
```python
def parse_timestamp(ts_str):
    parts = ts_str.strip("[]").split(":")
    if len(parts) == 3:
        a, b = int(parts[0]), int(parts[1])
        if a < 60:  # MM:SS:cs format (meetings under 1 hour)
            return a * 60 + b
        else:  # HH:MM:SS format (long meetings)
            return a * 3600 + b * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0
```

## Clickable Timestamps in UI

Timestamps are stored as Tiptap `code` marks (rendered as `<code>` elements):
```python
# In note content builder
parts.append({
    "type": "text",
    "marks": [{"type": "code"}],
    "text": f"\u23F5 {timestamp}",  # ⏵ play symbol
})
```

Frontend click handler in `NotesEditorView.tsx`:
- CSS styles `<code>` elements as clickable indigo badges
- Click handler parses time from text, calls `audioRef.current.currentTime = seconds`
- Audio player is `sticky` at top of editor for always-visible playback

## Corner Cases & Solutions

### 1. Repetition Loops
**Problem:** Gemini gets stuck repeating phrases on unclear audio sections.
**Solution:** Anti-repetition instruction in prompt + temperature 0.2 + post-processing regex.
**Detection:** `re.findall(r'(.{10,50}?)\1{3,}', text)` — if matches found, clean with `re.sub`.

### 2. Company Name Confusion
**Problem:** Similar-sounding companies confused (满帮↔美团, 金山云↔京象雲).
**Solution:** Vocabulary context with explicit "CRITICAL: X != Y" distinctions.
**Prevention:** Add `common_misrecognitions` entries when you find new confusions.

### 3. Slang/Shorthand → Wrong Text
**Problem:** Analysts use shorthand (巴巴 = Alibaba, 88 = Alibaba) that Gemini doesn't know.
**Solution:** Aliases list in vocabulary with explicit notes explaining the shorthand.

### 4. AI Model Names
**Problem:** 千问 (Qianwen/Qwen) transcribed as 钱问 (money question).
**Solution:** Add AI model names to vocabulary under the parent company's products list.

### 5. Audio Quality Drops
**Problem:** Remote speakers on bad connections produce garbled audio.
**Solution:** Anti-repetition instruction tells Gemini to write [audio unclear] instead of guessing.
**Observation:** Timothy's section (weaker mic) consistently has lower quality across all methods.

### 6. Traditional vs Simplified Chinese
**Problem:** Gemini sometimes outputs Traditional Chinese (繁体) instead of Simplified (简体).
**Observation:** This happens naturally and is not an error — many HK/TW financial meetings use Traditional. For mainland China meetings, add "Output in Simplified Chinese (简体中文)" to the prompt.

### 7. Deepgram Failure on Chinese
**Problem:** Deepgram nova-2 detected Chinese audio as English and returned empty transcript.
**Solution:** Do not use Deepgram for Chinese or EN/ZH mixed audio. Use Gemini or SenseVoice instead.

### 8. Base64 Size Limit
**Problem:** Gemini inline audio limit is ~20MB base64.
**Solution:** OPUS at 48kbps = ~7MB for 30 min (well under limit). For longer meetings, use Gemini File API or split into segments with ffmpeg.

## A/B Test Results Summary

| Method | Quality | Time | Cost | Repetition | Code-Switch |
|--------|---------|------|------|------------|-------------|
| **Gemini native audio + vocab** | 95%+ | 69s | $0.06 | None (with prompt fix) | Excellent |
| Gemini native audio (no vocab) | 90% | 50s | $0.04 | Bad (41K chars loops) | Excellent |
| SenseVoice + Gemini polish | 90% | 308s | $0.02 | None | Good (after polish) |
| Deepgram nova-2 | Failed | 3s | $0.10 | N/A | Failed |

**Winner:** Gemini native audio + vocabulary context + anti-repetition prompt.

## Cost at Scale

| Meeting Length | Input Tokens | Output Tokens | Cost |
|---------------|-------------|---------------|------|
| 15 min | ~30K | ~3K | ~$0.02 |
| 30 min | ~58K | ~6K | ~$0.04-0.06 |
| 60 min | ~115K | ~12K | ~$0.08-0.12 |
| 100 meetings/month | — | — | ~$4-12/mo |

## File Locations

| File | Purpose |
|------|---------|
| `tools/audio_recorder/meeting_vocabulary.json` | Domain vocabulary (companies, terms, corrections) |
| `tools/audio_recorder/ab_test_transcript.py` | A/B test runner (Methods A, B, C) |
| `tools/audio_recorder/polish_transcript.py` | V1 polish (dictionary + LLM, for local mode) |
| `tools/audio_recorder/polish_transcript_v2.py` | V2 polish (speakers + key points + bold) |
| `tools/audio_recorder/create_v2_note.py` | Creates note with clickable timestamps |
| `tools/audio_recorder/parse_sensevoice.py` | SenseVoice output parser |
| `tools/audio_recorder/test_models.py` | Model comparison test script |
| `docs/transcription_ab_test_results.md` | Full A/B test comparison |
| `docs/meeting_transcription_design.md` | Architecture design document |

## Building a New Transcription Pipeline — Checklist

1. **Prepare vocabulary file** for the target domain/sector
2. **Build prompt** with vocabulary context + anti-repetition instruction
3. **Set temperature to 0.2**, maxOutputTokens to 65536
4. **Send audio as inline_data** (OPUS preferred, <20MB)
5. **Post-process:** clean repetition loops with regex
6. **Parse timestamps** correctly (MM:SS:cs for <1hr meetings)
7. **Store in notes** with Tiptap editor content + audio player + clickable timestamps
8. **Test on sample audio** before processing batch
9. **Add new misrecognitions** to vocabulary file as you find them
10. **Save raw + polished versions** for comparison and debugging

## Future Improvements

- **Gemini File API** for meetings >20MB
- **Streaming transcription** for live meetings (SenseVoice needed — Gemini doesn't stream audio)
- **Speaker enrollment** — pre-register analyst voices for automatic named speaker detection
- **Auto-vocabulary** — LLM extracts company/term list from meeting invite/agenda before transcription
- **Multi-pass verification** — run SenseVoice + Gemini, merge best segments from each
- **pyannote diarization** — voice-based speaker separation for local mode
- **Financial NER post-processing** — extract and standardize ticker symbols, numbers, dates
