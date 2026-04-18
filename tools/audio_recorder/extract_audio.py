"""
extract_audio.py -- Extract audio from iPhone screen recordings (MP4/MOV)
and convert to OPUS for the transcription pipeline.

iOS Screen Recording captures system audio (both sides of a Zoom/Webex call)
as part of a video file. This script strips the video track and produces a
size-efficient OPUS file identical to what record_meeting.py outputs.

Usage:
    python extract_audio.py recording.mp4                    # -> recording.opus
    python extract_audio.py recording.mov -o meeting.opus    # custom output name
    python extract_audio.py *.mp4                            # batch mode

Requires ffmpeg: winget install ffmpeg

Output: 48 kbps mono OPUS with LUFS normalization (same as convert_to_opus.py).
A 1-hour screen recording (~500 MB video) produces ~22 MB of audio.
"""
import sys
import os
import argparse
import subprocess
import shutil
from pathlib import Path


def check_ffmpeg():
    return shutil.which("ffmpeg") is not None


def extract_audio(input_path: str, output_path: str | None = None) -> str:
    """Extract audio from a video file to OPUS.

    Uses the same ffmpeg settings as convert_to_opus.py:
    - 48 kHz sample rate
    - Mono (downmix from stereo)
    - 48 kbps bitrate (VoIP-optimized)
    - LUFS loudness normalization (broadcast standard)
    """
    inp = Path(input_path)
    if not inp.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_path:
        out = Path(output_path)
    else:
        out = inp.with_suffix(".opus")

    # Two-pass loudness normalization requires measuring first, then encoding.
    # Single-pass with loudnorm filter is close enough for speech and much faster.
    cmd = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-vn",                          # strip video
        "-ac", "1",                     # mono
        "-ar", "48000",                 # 48 kHz
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",  # LUFS normalization
        "-c:a", "libopus",
        "-b:a", "48k",
        "-application", "voip",
        str(out),
    ]

    print(f"Extracting audio: {inp.name} -> {out.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error:\n{result.stderr}", file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed with return code {result.returncode}")

    in_size  = inp.stat().st_size / (1024 * 1024)
    out_size = out.stat().st_size / (1024 * 1024)
    print(f"Done: {in_size:.1f} MB -> {out_size:.1f} MB ({out_size/in_size*100:.1f}% of original)")
    return str(out)


def main():
    parser = argparse.ArgumentParser(
        description="Extract audio from iPhone screen recordings to OPUS"
    )
    parser.add_argument(
        "inputs", nargs="+",
        help="Input video file(s) — MP4, MOV, or any ffmpeg-supported format"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path (only valid with a single input file)"
    )
    args = parser.parse_args()

    if not check_ffmpeg():
        print("Error: ffmpeg not found. Install with: winget install ffmpeg", file=sys.stderr)
        sys.exit(1)

    if args.output and len(args.inputs) > 1:
        print("Error: -o/--output can only be used with a single input file", file=sys.stderr)
        sys.exit(1)

    for inp in args.inputs:
        try:
            extract_audio(inp, args.output)
        except Exception as e:
            print(f"Error processing {inp}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
