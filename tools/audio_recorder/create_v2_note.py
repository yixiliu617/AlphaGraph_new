"""Create V2 note with vocabulary-improved transcript and clickable timestamps."""
import json
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

NOTE_ID = "f2599776-b322-4ee3-afa0-36f6db49f98e"  # V2 note

# Load improved transcript (V3 — with vocab + anti-repetition)
with open("tools/audio_recorder/ab_test_results/method_C_gemini_v3.txt", "r", encoding="utf-8") as f:
    text = f.read()


def parse_timestamp(ts_str):
    """Parse MM:SS:cs or HH:MM:SS to seconds. Gemini outputs MM:SS:centiseconds for short meetings."""
    parts = ts_str.strip("[]").split(":")
    if len(parts) == 3:
        a, b, c = int(parts[0]), int(parts[1]), int(parts[2].split(".")[0])
        # If first number < 60, treat as MM:SS:cs (centiseconds)
        # This is the format Gemini uses for meetings under 1 hour
        if a < 60:
            return a * 60 + b
        else:
            return a * 3600 + b * 60 + c
    elif len(parts) == 2:
        m, s = int(parts[0]), int(parts[1].split(".")[0])
        return m * 60 + s
    return 0


def line_to_tiptap_parts(line):
    """Convert a markdown line to Tiptap content parts, with timestamps as special marks."""
    parts = []

    # Extract leading timestamp [HH:MM:SS]
    ts_match = re.match(r'^\[(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\]\s*', line)
    if ts_match:
        ts_str = ts_match.group(1)
        seconds = parse_timestamp(f"[{ts_str}]")
        # Timestamp as a specially formatted text with data attribute encoded in the text
        parts.append({
            "type": "text",
            "marks": [{"type": "code"}],
            "text": f"\u23F5 {ts_str}",  # ⏵ play symbol + timestamp
        })
        parts.append({"type": "text", "text": " "})
        line = line[ts_match.end():]

    # Parse **bold** segments
    segs = line.split("**")
    for i, seg in enumerate(segs):
        if not seg:
            continue
        if i % 2 == 1:
            parts.append({"type": "text", "marks": [{"type": "bold"}], "text": seg})
        else:
            parts.append({"type": "text", "text": seg})

    return parts


# Build editor content
content_blocks = [
    {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "V2: Gemini + Vocabulary Context"}]},
    {"type": "paragraph", "content": [
        {"type": "text", "text": "Gemini 2.5 Flash native audio + meeting_vocabulary.json | 158s | ~$0.06 | "},
        {"type": "text", "marks": [{"type": "bold"}], "text": "Click any timestamp to play audio from that point"},
    ]},
    {"type": "paragraph", "content": [
        {"type": "text", "marks": [{"type": "bold"}], "text": "Fixes over V1: "},
        {"type": "text", "text": "YMM/Manbang (was Meituan), Alibaba (was 88), Kingsoft Cloud, driver matching, Qwen"},
    ]},
]

for line in text.split("\n"):
    line = line.strip()
    if not line:
        continue
    if line.startswith("---"):
        content_blocks.append({"type": "horizontalRule"})
        continue

    parts = line_to_tiptap_parts(line)
    if parts:
        content_blocks.append({"type": "paragraph", "content": parts})

editor_content = {"type": "doc", "content": content_blocks}

resp = requests.put(f"http://localhost:8000/api/v1/notes/{NOTE_ID}", json={
    "editor_content": editor_content,
})
print(f"Update: {resp.status_code}, blocks: {len(content_blocks)}")
