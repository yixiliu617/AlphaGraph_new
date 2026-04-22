"""
Polish ASR transcript v2 — adds speaker labels, formatting, and key point extraction.

Usage:
    python tools/audio_recorder/polish_transcript_v2.py <sensevoice.json> [--output polished.json]
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"


POLISH_PROMPT = """You are a professional financial transcript editor for a Chinese sell-side analyst morning call.

You are given a raw ASR transcript (from SenseVoice) of a Chinese financial meeting with English code-switching. The transcript has segments with language and emotion tags.

Your tasks:
1. **Fix ASR errors** — correct garbled English terms, company names, financial jargon
2. **Identify speakers** — detect speaker changes from context (analyst name mentions, topic shifts, Q&A patterns). Label as "主持人" (host), "分析师A/B/C" (Analyst A/B/C), "提问者" (questioner), etc. If names are mentioned (Ronald, Lincoln, etc.), use their names.
3. **Format as structured paragraphs** — each speaker turn is a separate paragraph
4. **Bold key points** — wrap key financial data, price targets, stock recommendations, and important conclusions in **bold**

Return a JSON object with this format:
{
  "speakers": ["Ronald (Lead Analyst)", "Lincoln (Gaming/Entertainment)", "Tian (Data Center/Property)", "提问者"],
  "key_points": [
    "**BABA top pick** — AI/cloud thesis, Kingsoft Cloud as key idea",
    "**PDD easy base** — Q1 topline acceleration, transaction service revenue growing"
  ],
  "paragraphs": [
    {"speaker": "Ronald", "text": "不仅未来几个星期应该能回到一个AI带动的narrative..."},
    {"speaker": "提问者", "text": "我有一个很快的问题，关于拼多多的..."},
    {"speaker": "Ronald", "text": "对，但是他每年的transaction services revenue..."}
  ]
}

Common ASR errors to fix:
- pesgogle/pesgoogle → Google, hperscaler → hyperscaler, narrator → narrative
- 爸爸/巴巴 → 阿里巴巴, 多多 → 拼多多, 题目 → Temu, 克林 → Kling
- pricingcing → pricing, erning → earnings, acce → accelerate
- CMO → consensus, count change → guidance change
- hay scale → hyperscaler, bu model → business model
"""

CHUNK_SIZE = 3000


def call_gemini(prompt, max_tokens=8192):
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
    }
    resp = requests.post(URL, json=payload, timeout=120)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="SenseVoice JSON output file")
    parser.add_argument("--output", "-o", help="Output file")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    print(f"Input: {len(segments)} segments")

    # Build raw text with segment markers
    raw_parts = []
    for i, seg in enumerate(segments):
        lang = seg.get("lang", "zh")
        text = seg.get("text", "").strip()
        if text:
            raw_parts.append(f"[{lang}] {text}")

    raw_text = "\n".join(raw_parts)
    print(f"Raw text: {len(raw_text)} chars")

    # Process in chunks
    chunks = []
    for i in range(0, len(raw_text), CHUNK_SIZE):
        chunks.append(raw_text[i:i + CHUNK_SIZE])

    print(f"Processing {len(chunks)} chunks...")

    all_paragraphs = []
    all_key_points = []
    speakers_seen = set()

    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i + 1}/{len(chunks)}...", end=" ", flush=True)

        prompt = f"{POLISH_PROMPT}\n\nRaw transcript chunk {i + 1}/{len(chunks)}:\n\n{chunk}"

        try:
            result_text = call_gemini(prompt, max_tokens=16384)
            start = result_text.find("{")
            end = result_text.rfind("}")

            if start != -1 and end != -1:
                result = json.loads(result_text[start:end + 1])
                paragraphs = result.get("paragraphs", [])
                key_points = result.get("key_points", [])
                speakers = result.get("speakers", [])

                all_paragraphs.extend(paragraphs)
                all_key_points.extend(key_points)
                speakers_seen.update(speakers)
                print(f"{len(paragraphs)} paragraphs, {len(key_points)} key points")
            else:
                print("No JSON found, keeping raw")
                all_paragraphs.append({"speaker": "?", "text": chunk})
        except Exception as e:
            print(f"ERROR: {e}")
            all_paragraphs.append({"speaker": "?", "text": chunk})

        if i < len(chunks) - 1:
            time.sleep(3)

    # Deduplicate key points
    seen_kp = set()
    unique_kp = []
    for kp in all_key_points:
        if kp not in seen_kp:
            seen_kp.add(kp)
            unique_kp.append(kp)

    output = {
        "speakers": sorted(speakers_seen),
        "key_points": unique_kp,
        "paragraphs": all_paragraphs,
        "stats": {
            "total_segments": len(segments),
            "total_paragraphs": len(all_paragraphs),
            "total_key_points": len(unique_kp),
            "speakers_detected": len(speakers_seen),
        },
    }

    # Save JSON
    out_path = args.output or args.input.replace(".json", "_polished_v2.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {out_path}")

    # Also save formatted text
    txt_path = out_path.replace(".json", ".txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("KEY POINTS\n")
        f.write("=" * 50 + "\n")
        for kp in unique_kp:
            f.write(f"  {kp}\n")
        f.write("\n\nTRANSCRIPT\n")
        f.write("=" * 50 + "\n\n")
        for para in all_paragraphs:
            speaker = para.get("speaker", "?")
            text = para.get("text", "")
            f.write(f"[{speaker}]\n{text}\n\n")
    print(f"Saved: {txt_path}")

    # Print summary
    print(f"\nSpeakers detected: {sorted(speakers_seen)}")
    print(f"Key points: {len(unique_kp)}")
    print(f"Paragraphs: {len(all_paragraphs)}")


if __name__ == "__main__":
    main()
