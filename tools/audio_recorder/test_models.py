"""
Test script for SenseVoice and kotoba-whisper ASR models.
Compares transcription quality across models on the same audio.

Usage:
    python tools/audio_recorder/test_models.py <audio_file>
    python tools/audio_recorder/test_models.py <audio_file> --model sensevoice
    python tools/audio_recorder/test_models.py <audio_file> --model kotoba
    python tools/audio_recorder/test_models.py <audio_file> --model whisper
    python tools/audio_recorder/test_models.py <audio_file> --model all
"""

import argparse
import os
import sys
import time


def test_sensevoice(audio_file, language="auto"):
    """Test SenseVoice model."""
    print("=" * 60)
    print("  SenseVoice-Small (FunASR)")
    print("=" * 60)

    from funasr import AutoModel

    print("Loading model...")
    start = time.time()
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cpu",
        disable_update=True,
    )
    print(f"  Loaded in {time.time()-start:.1f}s")

    print(f"Transcribing: {audio_file}")
    start = time.time()
    result = model.generate(
        input=audio_file,
        cache={},
        language=language,
        use_itn=True,
        batch_size_s=60,
    )
    elapsed = time.time() - start

    print(f"  Done in {elapsed:.1f}s ({len(result)} segments)")
    print()

    full_text = ""
    for r in result:
        text = r.get("text", "")
        full_text += text + " "
        text_display = text.encode("ascii", "replace").decode()[:120]
        print(f"  {text_display}")

    # Save
    out_path = audio_file.rsplit(".", 1)[0] + "_sensevoice.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_text.strip())
    print(f"\n  Saved: {out_path}")
    return full_text


def test_kotoba_whisper(audio_file):
    """Test kotoba-whisper model."""
    print("=" * 60)
    print("  kotoba-whisper v2.1 (Japanese-optimized Whisper)")
    print("=" * 60)

    import torch
    from transformers import pipeline

    print("Loading model (first run downloads ~3GB)...")
    start = time.time()
    pipe = pipeline(
        "automatic-speech-recognition",
        model="kotoba-tech/kotoba-whisper-v2.2",
        torch_dtype=torch.float32,
        device="cpu",
        chunk_length_s=30,
        batch_size=1,
    )
    print(f"  Loaded in {time.time()-start:.1f}s")

    print(f"Transcribing: {audio_file}")
    start = time.time()
    result = pipe(
        audio_file,
        return_timestamps=True,
        generate_kwargs={
            "language": "ja",
            "task": "transcribe",
        },
    )
    elapsed = time.time() - start

    text = result.get("text", "")
    chunks = result.get("chunks", [])
    print(f"  Done in {elapsed:.1f}s ({len(chunks)} chunks)")
    print()

    for chunk in chunks[:15]:
        ts = chunk.get("timestamp", (0, 0))
        t = chunk.get("text", "")
        t_display = t.encode("ascii", "replace").decode()[:100]
        start_s = ts[0] if ts[0] else 0
        end_s = ts[1] if ts[1] else 0
        print(f"  [{start_s:6.1f}-{end_s:6.1f}] {t_display}")

    out_path = audio_file.rsplit(".", 1)[0] + "_kotoba.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n  Saved: {out_path}")
    return text


def test_faster_whisper(audio_file, language=None):
    """Test faster-whisper model."""
    print("=" * 60)
    print("  faster-whisper large-v3")
    print("=" * 60)

    from faster_whisper import WhisperModel

    print("Loading model...")
    start = time.time()
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    print(f"  Loaded in {time.time()-start:.1f}s")

    print(f"Transcribing: {audio_file}")
    start = time.time()
    segments, info = model.transcribe(
        audio_file,
        language=language,
        beam_size=5,
        vad_filter=True,
        initial_prompt="Financial meeting discussion about earnings, revenue, guidance, ROE, EBITDA, semiconductor, DRAM, HBM.",
    )
    segments = list(segments)
    elapsed = time.time() - start

    print(f"  Done in {elapsed:.1f}s ({len(segments)} segments)")
    print(f"  Detected language: {info.language} ({info.language_probability:.0%})")
    print()

    full_text = ""
    for seg in segments[:15]:
        text = seg.text.strip()
        full_text += text + " "
        text_display = text.encode("ascii", "replace").decode()[:100]
        print(f"  [{seg.start:6.1f}-{seg.end:6.1f}] {text_display}")

    out_path = audio_file.rsplit(".", 1)[0] + "_whisper.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_text.strip())
    print(f"\n  Saved: {out_path}")
    return full_text


def main():
    parser = argparse.ArgumentParser(description="Test ASR models")
    parser.add_argument("audio", help="Audio file to transcribe")
    parser.add_argument("--model", default="sensevoice",
                        choices=["sensevoice", "kotoba", "whisper", "all"],
                        help="Model to test")
    parser.add_argument("--language", default=None,
                        help="Language hint (auto, en, zh, ja, ko)")
    args = parser.parse_args()

    if not os.path.exists(args.audio):
        print(f"File not found: {args.audio}")
        sys.exit(1)

    size_mb = os.path.getsize(args.audio) / 1024 / 1024
    print(f"Audio file: {args.audio} ({size_mb:.1f} MB)")
    print()

    if args.model in ("sensevoice", "all"):
        test_sensevoice(args.audio, args.language or "auto")
        print()

    if args.model in ("kotoba", "all"):
        test_kotoba_whisper(args.audio)
        print()

    if args.model in ("whisper", "all"):
        test_faster_whisper(args.audio, args.language)
        print()


if __name__ == "__main__":
    main()
