"""
Post-Meeting Transcription using Faster-Whisper
Transcribes audio recordings with high accuracy using local CPU.
Supports WAV, OPUS, MP3, and other common formats.

Usage:
    python transcribe_recording.py recording.opus
    python transcribe_recording.py recording.wav --model medium
    python transcribe_recording.py recordings/  # Transcribe all audio in folder

Models (accuracy vs speed tradeoff):
    tiny   - Fastest, lowest accuracy (~10x realtime on CPU)
    base   - Fast, decent accuracy (~5x realtime)
    small  - Balanced (~2x realtime)
    medium - High accuracy (~1x realtime) [RECOMMENDED]
    large  - Highest accuracy (~0.5x realtime, needs more RAM)

Requires:
    pip install faster-whisper
"""

import sys
import os
import argparse
import datetime
from pathlib import Path

# Fix Windows console encoding for Chinese/Unicode
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

try:
    from faster_whisper import WhisperModel
except ImportError:
    print("Missing dependency. Install with:")
    print("  pip install faster-whisper")
    sys.exit(1)


def format_timestamp(seconds):
    """Convert seconds to HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def transcribe_file(audio_path, model_size="medium", output_path=None):
    """Transcribe a single audio file."""
    audio_path = Path(audio_path)

    if not audio_path.exists():
        print(f"Error: File not found: {audio_path}")
        return False

    print(f"Loading Whisper model '{model_size}' (first run downloads the model)...")
    print("This may take a moment...")

    # Use CPU with int8 quantization for efficiency
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"\nTranscribing: {audio_path.name}")
    print("-" * 60)

    # Transcribe (auto-detect language)
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,  # Filter out silence
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    print(f"Detected language: {info.language} (confidence: {info.language_probability:.1%})")
    print(f"Duration: {info.duration:.1f} seconds ({info.duration/60:.1f} minutes)")
    print("-" * 60)
    print()

    # Collect segments
    transcript_lines = []
    full_text_parts = []

    for segment in segments:
        timestamp = format_timestamp(segment.start)
        line = f"[{timestamp}] {segment.text.strip()}"
        print(line)
        transcript_lines.append(line)
        full_text_parts.append(segment.text.strip())

    # Determine output path
    if output_path is None:
        transcripts_dir = Path(__file__).parent / "transcripts"
        transcripts_dir.mkdir(exist_ok=True)
        output_path = transcripts_dir / f"{audio_path.stem}_transcript.txt"

    # Save transcript
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"Transcript: {audio_path.name}\n")
        f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Model: whisper-{model_size}\n")
        f.write(f"Duration: {info.duration/60:.1f} minutes\n")
        f.write("=" * 60 + "\n\n")

        # Timestamped version
        f.write("## Timestamped Transcript\n\n")
        for line in transcript_lines:
            f.write(line + "\n")

        # Clean version
        f.write("\n\n## Full Text\n\n")
        f.write(" ".join(full_text_parts))

    print()
    print("-" * 60)
    print(f"Transcript saved: {output_path}")

    return True


def transcribe_folder(folder_path, model_size="medium"):
    """Transcribe all audio files in a folder."""
    folder = Path(folder_path)

    # Find all supported audio/video files (Whisper handles video natively via ffmpeg)
    audio_files = []
    for ext in ['*.opus', '*.wav', '*.mp3', '*.m4a', '*.ogg', '*.mp4', '*.mov', '*.mkv']:
        audio_files.extend(folder.glob(ext))

    if not audio_files:
        print(f"No audio files found in: {folder}")
        return

    # Sort by name
    audio_files = sorted(audio_files, key=lambda x: x.name)

    print(f"Found {len(audio_files)} audio file(s) to transcribe")
    print()

    # Load model once
    print(f"Loading Whisper model '{model_size}'...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    for i, audio_file in enumerate(audio_files, 1):
        print(f"\n[{i}/{len(audio_files)}] Processing: {audio_file.name}")
        print("=" * 60)

        # Check if already transcribed
        transcript_path = folder.parent / "transcripts" / f"{audio_file.stem}_transcript.txt"
        if transcript_path.exists():
            print(f"Already transcribed, skipping. Delete {transcript_path.name} to re-transcribe.")
            continue

        segments, info = model.transcribe(
            str(audio_file),
            beam_size=5,
            vad_filter=True,
        )

        transcript_lines = []
        full_text_parts = []

        for segment in segments:
            timestamp = format_timestamp(segment.start)
            line = f"[{timestamp}] {segment.text.strip()}"
            print(line)
            transcript_lines.append(line)
            full_text_parts.append(segment.text.strip())

        # Save
        transcripts_dir = Path(__file__).parent / "transcripts"
        transcripts_dir.mkdir(exist_ok=True)
        output_path = transcripts_dir / f"{audio_file.stem}_transcript.txt"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"Transcript: {audio_file.name}\n")
            f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"Model: whisper-{model_size}\n")
            f.write(f"Duration: {info.duration/60:.1f} minutes\n")
            f.write("=" * 60 + "\n\n")
            f.write("## Timestamped Transcript\n\n")
            for line in transcript_lines:
                f.write(line + "\n")
            f.write("\n\n## Full Text\n\n")
            f.write(" ".join(full_text_parts))

        print(f"\nSaved: {output_path}")

    print("\n" + "=" * 60)
    print("All transcriptions complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe meeting recordings using Whisper"
    )
    parser.add_argument(
        'input',
        help='Audio file (WAV/OPUS/MP3) or folder containing audio files'
    )
    parser.add_argument(
        '--model', '-m',
        default='medium',
        choices=['tiny', 'base', 'small', 'medium', 'large'],
        help='Whisper model size (default: medium)'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output transcript file path'
    )

    args = parser.parse_args()
    input_path = Path(args.input)

    if input_path.is_dir():
        transcribe_folder(input_path, args.model)
    elif input_path.is_file():
        transcribe_file(input_path, args.model, args.output)
    else:
        print(f"Error: Path not found: {input_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
