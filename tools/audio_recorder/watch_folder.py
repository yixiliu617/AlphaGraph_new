"""
watch_folder.py -- Watch a folder (e.g. iCloud sync) for new screen recordings
and auto-extract audio + trigger transcription.

Designed for the iPhone screen-recording workflow:
  1. iOS Screen Recording saves to Camera Roll
  2. iCloud/OneDrive syncs the .mp4/.mov to a PC folder
  3. This script detects the new file, extracts audio to OPUS,
     and optionally runs transcription (Whisper or Deepgram)

Usage:
    python watch_folder.py ~/iCloud/Screen\ Recordings/
    python watch_folder.py C:/Users/You/OneDrive/Recordings --transcribe
    python watch_folder.py ./incoming --transcribe --speakers

Options:
    --transcribe     Auto-run local Whisper transcription after extraction
    --speakers       Use Deepgram (cloud) for speaker-diarized transcription
    --poll-interval  Seconds between folder scans (default: 10)
"""
import sys
import os
import time
import argparse
from pathlib import Path

# Resolve paths relative to this script so imports work
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from extract_audio import extract_audio, check_ffmpeg

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
PROCESSED_LOG = SCRIPT_DIR / "recordings" / ".processed_files.txt"


def load_processed() -> set[str]:
    if not PROCESSED_LOG.exists():
        return set()
    return set(PROCESSED_LOG.read_text(encoding="utf-8").strip().splitlines())


def mark_processed(filepath: str) -> None:
    PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(filepath + "\n")


def transcribe_local(opus_path: str, model: str = "medium") -> None:
    """Run local Whisper transcription."""
    try:
        import subprocess
        script = SCRIPT_DIR / "transcribe_recording.py"
        cmd = [sys.executable, str(script), opus_path, "--model", model]
        print(f"  Transcribing (local Whisper {model})...")
        subprocess.run(cmd, check=True)
    except Exception as e:
        print(f"  Transcription failed: {e}")


def transcribe_speakers(opus_path: str) -> None:
    """Run Deepgram transcription with speaker diarization."""
    try:
        import subprocess
        script = SCRIPT_DIR / "transcribe_with_speakers.py"
        cmd = [sys.executable, str(script), opus_path]
        print(f"  Transcribing (Deepgram with speakers)...")
        subprocess.run(cmd, check=True)
    except Exception as e:
        print(f"  Transcription failed: {e}")


def scan_folder(watch_dir: Path, do_transcribe: bool, do_speakers: bool) -> int:
    """Scan for new video files, extract audio, optionally transcribe. Returns count processed."""
    processed = load_processed()
    count = 0

    for f in sorted(watch_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if str(f) in processed:
            continue

        # Skip files still being written (size changed in last 5 seconds)
        try:
            size1 = f.stat().st_size
            time.sleep(2)
            size2 = f.stat().st_size
            if size1 != size2:
                print(f"  Skipping {f.name} (still syncing...)")
                continue
        except OSError:
            continue

        print(f"\nNew recording detected: {f.name}")
        try:
            opus_path = extract_audio(str(f))
            mark_processed(str(f))
            count += 1

            if do_speakers:
                transcribe_speakers(opus_path)
            elif do_transcribe:
                transcribe_local(opus_path)

        except Exception as e:
            print(f"  Error: {e}")

    return count


def main():
    parser = argparse.ArgumentParser(
        description="Watch a folder for new screen recordings and auto-process"
    )
    parser.add_argument("folder", help="Folder to watch (e.g. iCloud sync directory)")
    parser.add_argument("--transcribe", action="store_true",
                        help="Auto-transcribe with local Whisper after extraction")
    parser.add_argument("--speakers", action="store_true",
                        help="Auto-transcribe with Deepgram (cloud, speaker diarization)")
    parser.add_argument("--poll-interval", type=int, default=10,
                        help="Seconds between folder scans (default: 10)")
    parser.add_argument("--once", action="store_true",
                        help="Scan once and exit (no continuous watching)")
    args = parser.parse_args()

    if not check_ffmpeg():
        print("Error: ffmpeg not found. Install with: winget install ffmpeg", file=sys.stderr)
        sys.exit(1)

    watch_dir = Path(args.folder).resolve()
    if not watch_dir.is_dir():
        print(f"Error: {watch_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Watching: {watch_dir}")
    print(f"Transcription: {'Deepgram (speakers)' if args.speakers else 'Whisper (local)' if args.transcribe else 'disabled'}")
    print(f"Poll interval: {args.poll_interval}s")
    print("Press Ctrl+C to stop.\n")

    try:
        if args.once:
            n = scan_folder(watch_dir, args.transcribe, args.speakers)
            print(f"\nProcessed {n} file(s).")
        else:
            while True:
                scan_folder(watch_dir, args.transcribe, args.speakers)
                time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
