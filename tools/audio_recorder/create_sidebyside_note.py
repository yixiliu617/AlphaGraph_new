"""Create a side-by-side JA/EN transcript note using Tiptap table."""
import json
import requests
from pathlib import Path

NOTE_ID = "ced6e5c5-d067-4d8e-ab69-b12c86baca4b"

with open("tools/audio_recorder/ab_test_results/japanese_meeting_paired.json", "r", encoding="utf-8") as f:
    data = json.load(f)

segments = data.get("segments", [])
key_topics = data.get("key_topics", [])

content_blocks = [
    {"type": "heading", "attrs": {"level": 2}, "content": [
        {"type": "text", "text": "SoftBank Group Q3 FY2025 Earnings"}
    ]},
    {"type": "paragraph", "content": [
        {"type": "text", "text": "10 min | Gemini 2.5 Flash | 64 segments | "},
        {"type": "text", "marks": [{"type": "bold"}], "text": "Click timestamps to play audio"},
    ]},
]

# Key topics
content_blocks.append({"type": "heading", "attrs": {"level": 3}, "content": [
    {"type": "text", "text": "Key Topics"}
]})
kp_items = []
for topic in key_topics:
    kp_items.append({"type": "listItem", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": topic}]}
    ]})
content_blocks.append({"type": "bulletList", "content": kp_items})

# Side-by-side table
content_blocks.append({"type": "heading", "attrs": {"level": 3}, "content": [
    {"type": "text", "text": "Transcript (Japanese / English)"}
]})

# Build table: header row + segment rows
header_row = {
    "type": "tableRow",
    "content": [
        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [
            {"type": "text", "marks": [{"type": "bold"}], "text": "Time"}
        ]}]},
        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [
            {"type": "text", "marks": [{"type": "bold"}], "text": "Japanese (Original)"}
        ]}]},
        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [
            {"type": "text", "marks": [{"type": "bold"}], "text": "English (Translation)"}
        ]}]},
    ],
}

rows = [header_row]
for seg in segments:
    ts = seg.get("ts", "")
    ja = seg.get("ja", "")
    en = seg.get("en", "")

    # Parse bold markers in JA and EN
    def parse_bold(text):
        parts = []
        segs = text.split("**")
        for i, s in enumerate(segs):
            if not s:
                continue
            if i % 2 == 1:
                parts.append({"type": "text", "marks": [{"type": "bold"}], "text": s})
            else:
                parts.append({"type": "text", "text": s})
        return parts if parts else [{"type": "text", "text": text}]

    row = {
        "type": "tableRow",
        "content": [
            {"type": "tableCell", "content": [{"type": "paragraph", "content": [
                {"type": "text", "marks": [{"type": "code"}], "text": ts},
            ]}]},
            {"type": "tableCell", "content": [{"type": "paragraph", "content": parse_bold(ja)}]},
            {"type": "tableCell", "content": [{"type": "paragraph", "content": parse_bold(en)}]},
        ],
    }
    rows.append(row)

content_blocks.append({"type": "table", "content": rows})

editor_content = {"type": "doc", "content": content_blocks}

resp = requests.put(f"http://localhost:8000/api/v1/notes/{NOTE_ID}", json={
    "title": "SoftBank Group Q3 FY2025 — Japanese/English Side-by-Side",
    "editor_content": editor_content,
})
print(f"Update: {resp.status_code}, blocks: {len(content_blocks)}, rows: {len(rows)}")
