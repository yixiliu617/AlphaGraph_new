"""
A/B Test: Compare transcription methods on the same audio file.

Methods:
  A: SenseVoice (local) → Gemini LLM polish (current pipeline)
  B: Deepgram nova-2 (cloud API) with diarization
  C: Gemini 2.5 Flash native audio (send audio directly to LLM)
  D: SenseVoice (local) + Deepgram (cloud) → merge best of both

Usage:
    python tools/audio_recorder/ab_test_transcript.py <audio_file> --methods A,B,C
    python tools/audio_recorder/ab_test_transcript.py <audio_file> --methods all
    python tools/audio_recorder/ab_test_transcript.py <audio_file> --methods B  # just Deepgram
    python tools/audio_recorder/ab_test_transcript.py <audio_file> --segment 0:300  # first 5 min only
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

OUT_DIR = Path("tools/audio_recorder/ab_test_results")


def extract_segment(audio_path, start_s=0, end_s=None):
    """Extract a segment of audio using ffmpeg. Returns path to temp file."""
    import subprocess

    if start_s == 0 and end_s is None:
        return audio_path

    out_path = str(OUT_DIR / "segment_temp.wav")
    cmd = ["ffmpeg", "-y", "-i", audio_path, "-ss", str(start_s), "-ac", "1", "-ar", "16000"]
    if end_s is not None:
        cmd.extend(["-to", str(end_s)])
    cmd.append(out_path)
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


# ---------------------------------------------------------------------------
# Method A: SenseVoice + Gemini polish (current pipeline)
# ---------------------------------------------------------------------------

def method_a_sensevoice(audio_path):
    """SenseVoice local ASR + Gemini LLM polish."""
    print("\n" + "=" * 60)
    print("  METHOD A: SenseVoice (local) + Gemini polish")
    print("=" * 60)

    from funasr import AutoModel

    print("  Loading SenseVoice...")
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cpu",
        disable_update=True,
    )

    print("  Transcribing...")
    start = time.time()
    result = model.generate(input=audio_path, cache={}, language="auto", use_itn=True, batch_size_s=60)
    elapsed = time.time() - start

    import re
    text = result[0].get("text", "")
    clean = re.sub(r"<\|[^|]+\|>", "", text)

    print(f"  Done: {elapsed:.1f}s, {len(clean)} chars")

    out = OUT_DIR / "method_A_sensevoice.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(clean)
    print(f"  Saved: {out}")
    return {"method": "A", "name": "SenseVoice + Gemini", "time": elapsed, "chars": len(clean), "file": str(out)}


# ---------------------------------------------------------------------------
# Method B: Deepgram nova-2 (cloud)
# ---------------------------------------------------------------------------

def method_b_deepgram(audio_path):
    """Deepgram nova-2 cloud API with diarization."""
    print("\n" + "=" * 60)
    print("  METHOD B: Deepgram nova-2 (cloud, with diarization)")
    print("=" * 60)

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        print("  SKIP: DEEPGRAM_API_KEY not set")
        return None

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    print(f"  Uploading {len(audio_data) // 1024}KB to Deepgram...")
    start = time.time()

    resp = requests.post(
        "https://api.deepgram.com/v1/listen",
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/ogg",
        },
        params={
            "model": "nova-2",
            "language": "zh",
            "detect_language": "true",
            "smart_format": "true",
            "punctuate": "true",
            "diarize": "true",
            "utterances": "true",
            "paragraphs": "true",
        },
        data=audio_data,
        timeout=600,
    )
    elapsed = time.time() - start
    resp.raise_for_status()
    result = resp.json()

    # Extract transcript with speakers
    lines = []
    utterances = result.get("results", {}).get("utterances", [])
    if utterances:
        speaker_map = {}
        for utt in utterances:
            spk_id = utt.get("speaker", 0)
            if spk_id not in speaker_map:
                speaker_map[spk_id] = f"Speaker {chr(65 + len(speaker_map))}"
            speaker = speaker_map[spk_id]
            text = utt.get("transcript", "")
            start_t = utt.get("start", 0)
            lines.append(f"[{start_t:.1f}s] [{speaker}] {text}")
    else:
        channels = result.get("results", {}).get("channels", [])
        if channels:
            alt = channels[0].get("alternatives", [{}])[0]
            paragraphs = alt.get("paragraphs", {}).get("paragraphs", [])
            for para in paragraphs:
                spk = para.get("speaker", 0)
                for sent in para.get("sentences", []):
                    lines.append(f"[Speaker {chr(65 + spk)}] {sent.get('text', '')}")

    full_text = "\n".join(lines)
    print(f"  Done: {elapsed:.1f}s, {len(lines)} utterances, {len(full_text)} chars")

    out = OUT_DIR / "method_B_deepgram.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(full_text)

    out_json = OUT_DIR / "method_B_deepgram.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  Saved: {out}")
    return {"method": "B", "name": "Deepgram nova-2", "time": elapsed, "chars": len(full_text), "file": str(out)}


# ---------------------------------------------------------------------------
# Method C: Gemini native audio understanding
# ---------------------------------------------------------------------------

def method_c_gemini_audio(audio_path):
    """Send audio directly to Gemini 2.5 Flash for transcription."""
    print("\n" + "=" * 60)
    print("  METHOD C: Gemini 2.5 Flash (native audio input)")
    print("=" * 60)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  SKIP: GEMINI_API_KEY not set")
        return None

    # Check file size — Gemini has limits
    file_size = os.path.getsize(audio_path)
    if file_size > 20 * 1024 * 1024:
        print(f"  File too large ({file_size // 1024 // 1024}MB). Extracting first 10 min...")
        audio_path = extract_segment(audio_path, 0, 600)
        file_size = os.path.getsize(audio_path)

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    print(f"  Sending {file_size // 1024}KB audio to Gemini...")

    mime = "audio/ogg" if audio_path.endswith(".opus") or audio_path.endswith(".ogg") else "audio/wav"

    prompt = """Transcribe this financial meeting audio. The meeting is primarily in Chinese (Mandarin) with English financial terms mixed in (code-switching).

Rules:
1. Transcribe EXACTLY what is said — do not summarize or paraphrase
2. Keep English financial terms in English (ROE, EBITDA, EPS, guidance, consensus, top line, etc.)
3. Identify different speakers and label them (Speaker A, Speaker B, etc.)
4. Format as paragraphs, one per speaker turn
5. Bold **key financial data points** (numbers, percentages, stock recommendations)
6. At the top, list the key discussion topics as bullet points

Format:
## Key Topics
- ...

## Transcript
**[Speaker A]** text here...

**[Speaker B]** text here..."""

    start = time.time()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": audio_b64}},
        ]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 65536},
    }

    resp = requests.post(url, json=payload, timeout=600)
    elapsed = time.time() - start
    resp.raise_for_status()

    result = resp.json()
    text = result["candidates"][0]["content"]["parts"][0]["text"]

    print(f"  Done: {elapsed:.1f}s, {len(text)} chars")

    out = OUT_DIR / "method_C_gemini_audio.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  Saved: {out}")

    # Token usage
    usage = result.get("usageMetadata", {})
    print(f"  Tokens — input: {usage.get('promptTokenCount', '?')}, output: {usage.get('candidatesTokenCount', '?')}")

    return {"method": "C", "name": "Gemini native audio", "time": elapsed, "chars": len(text), "file": str(out)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="A/B test transcription methods")
    parser.add_argument("audio", help="Audio file path")
    parser.add_argument("--methods", default="A,B,C", help="Methods to test (A,B,C or all)")
    parser.add_argument("--segment", help="Time segment as start:end in seconds (e.g., 0:300 for first 5 min)")
    args = parser.parse_args()

    if not os.path.exists(args.audio):
        print(f"File not found: {args.audio}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    audio_path = args.audio
    if args.segment:
        parts = args.segment.split(":")
        start_s = int(parts[0])
        end_s = int(parts[1]) if len(parts) > 1 else None
        print(f"Extracting segment: {start_s}s to {end_s}s...")
        audio_path = extract_segment(args.audio, start_s, end_s)

    file_size = os.path.getsize(audio_path)
    print(f"Audio: {audio_path} ({file_size // 1024}KB)")

    methods = args.methods.upper().split(",") if args.methods != "all" else ["A", "B", "C"]
    results = []

    if "A" in methods:
        r = method_a_sensevoice(audio_path)
        if r:
            results.append(r)

    if "B" in methods:
        r = method_b_deepgram(audio_path)
        if r:
            results.append(r)

    if "C" in methods:
        r = method_c_gemini_audio(audio_path)
        if r:
            results.append(r)

    # Summary
    print("\n" + "=" * 60)
    print("  COMPARISON SUMMARY")
    print("=" * 60)
    print(f"  {'Method':<30s} {'Time':>8s} {'Chars':>8s}")
    print("  " + "-" * 50)
    for r in results:
        print(f"  {r['name']:<30s} {r['time']:>7.1f}s {r['chars']:>7d}")

    print(f"\n  Results saved to: {OUT_DIR}/")
    print("  Compare the .txt files side by side to evaluate quality.")

    # Save summary
    with open(OUT_DIR / "summary.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
