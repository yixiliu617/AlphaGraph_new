"""
Polish a raw ASR transcript using LLM post-correction.
Fixes financial jargon, English/Chinese code-switching errors, company names.

Usage:
    python tools/audio_recorder/polish_transcript.py <input.txt> [--output polished.txt]
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

SYSTEM_PROMPT = """You are a professional financial transcript editor. You are given a raw ASR (automatic speech recognition) transcript from a Chinese financial analyst meeting that contains EN/ZH code-switching.

Your job is to fix ASR errors while preserving the original meaning EXACTLY. Do NOT add, remove, or paraphrase content.

Common ASR error patterns to fix:
1. English financial terms transcribed incorrectly:
   - pesgogle, pesgoogle -> Google
   - pricingcing, priing -> pricing
   - erning -> earnings
   - guidanceance -> guidance
   - investmentsments -> investments
   - formatat -> format
   - reevenue -> revenue
   - transactionact -> transaction
   - acce -> accelerate
   - el -> 加速 (context dependent)

2. Financial abbreviations in katakana/garbled form:
   - Fix to proper English: ROE, EBITDA, EPS, P/E, PE, PB, NPM, GPM, OPM, FCF, CAPEX, OPEX, IRR

3. Company/brand names:
   - 巴巴/爸爸 -> 阿里巴巴 (Alibaba) when context is about the company
   - 多多 -> 拼多多 (Pinduoduo)
   - 克林 -> Kling (AI model by Kuaishou)
   - 题目/team -> Temu (Pinduoduo's overseas platform)
   - narrator -> narrative
   - hay scale -> hyperscaler

4. Financial terms should be kept in their commonly-used form:
   - Keep English terms in English when commonly used that way in Chinese finance: top line, bottom line, guidance, consensus, outperformance, easy base, margin, revenue, earnings
   - Chinese financial terms should use standard characters: 营业利益 (not 营业利气), 一股当たり利益 -> 每股收益

5. Formatting:
   - Keep the transcript as continuous text
   - Do NOT add timestamps, speaker labels, or paragraph breaks that weren't in the original
   - Preserve the natural flow of the meeting discussion

Return ONLY the corrected transcript text, nothing else."""

CHUNK_SIZE = 2000  # characters per LLM call


def polish_with_gemini(text, api_key):
    """Polish transcript using Gemini."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    chunks = []
    for i in range(0, len(text), CHUNK_SIZE):
        chunks.append(text[i:i + CHUNK_SIZE])

    polished_parts = []
    for i, chunk in enumerate(chunks):
        print(f"  Polishing chunk {i + 1}/{len(chunks)}...", end=" ", flush=True)

        payload = {
            "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\nRaw transcript chunk:\n{chunk}"}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
        }

        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            result = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            polished_parts.append(result.strip())
            print("done")
        except Exception as e:
            print(f"ERROR: {e}")
            polished_parts.append(chunk)

        if i < len(chunks) - 1:
            time.sleep(2)

    return "\n".join(polished_parts)


def apply_dictionary(text):
    """Apply deterministic dictionary corrections before LLM pass."""
    replacements = {
        "pesgogle": "Google",
        "pesgoogle": "Google",
        "pricingcing": "pricing",
        "priing": "pricing",
        "erning": "earnings",
        "guidanceance": "guidance",
        "investmentsments": "investments",
        "formatat": "format",
        "reevenue": "revenue",
        "transactionact": "transaction",
        "conferenceference": "conference",
        "narrator": "narrative",
        "hay scale": "hyperscaler",
        "hperscaler": "hyperscaler",
    }

    for wrong, right in replacements.items():
        text = text.replace(wrong, right)

    return text


def main():
    parser = argparse.ArgumentParser(description="Polish ASR transcript with LLM")
    parser.add_argument("input", help="Input transcript file (.txt)")
    parser.add_argument("--output", "-o", help="Output file (default: input_polished.txt)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"File not found: {args.input}")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        raw_text = f.read()

    print(f"Input: {args.input} ({len(raw_text)} chars)")

    # Step 1: Dictionary corrections
    print("Step 1: Applying dictionary corrections...")
    text = apply_dictionary(raw_text)
    dict_changes = sum(1 for a, b in zip(raw_text, text) if a != b)
    print(f"  {dict_changes} characters changed")

    # Step 2: LLM polishing
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set, skipping LLM polishing")
        polished = text
    else:
        print(f"Step 2: LLM polishing ({len(text)} chars in {(len(text) // CHUNK_SIZE) + 1} chunks)...")
        polished = polish_with_gemini(text, api_key)

    # Save
    out_path = args.output or args.input.replace(".txt", "_polished.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(polished)

    print(f"\nSaved: {out_path} ({len(polished)} chars)")
    print(f"Original: {len(raw_text)} chars -> Polished: {len(polished)} chars")


if __name__ == "__main__":
    main()
