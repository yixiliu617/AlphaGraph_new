"""
Convert WAV files to OPUS for efficient storage.
Reduces file size by ~95% while maintaining transcription quality.

Usage:
    python convert_to_opus.py recording.wav           # Convert single file
    python convert_to_opus.py recordings              # Convert all WAVs in folder
    python convert_to_opus.py recordings --delete     # Convert and delete original WAVs

Requires: ffmpeg installed and in PATH
    - Windows: winget install ffmpeg
    - Or download from: https://ffmpeg.org/download.html
"""

import sys
import os
import argparse
import subprocess
import shutil
from pathlib import Path


def check_ffmpeg():
    """Check if ffmpeg is installed."""
    if shutil.which("ffmpeg") is None:
        print("ERROR: ffmpeg not found!")
        print()
        print("Install ffmpeg:")
        print("  Windows: winget install ffmpeg")
        print("  Or download from: https://ffmpeg.org/download.html")
        print()
        print("After installing, restart your terminal.")
        return False
    return True


def get_file_size_mb(path):
    """Get file size in MB."""
    return os.path.getsize(path) / (1024 * 1024)


def convert_wav_to_opus(wav_path, output_path=None, bitrate="48k", delete_original=False):
    """
    Convert a WAV file to OPUS format.

    Args:
        wav_path: Path to input WAV file
        output_path: Path for output OPUS file (default: same name with .opus)
        bitrate: Audio bitrate (default: 48k - good for speech)
        delete_original: Whether to delete the original WAV after conversion

    Returns:
        Path to output file, or None if failed
    """
    wav_path = Path(wav_path)

    if not wav_path.exists():
        print(f"Error: File not found: {wav_path}")
        return None

    if not wav_path.suffix.lower() == '.wav':
        print(f"Error: Not a WAV file: {wav_path}")
        return None

    # Determine output path
    if output_path is None:
        output_path = wav_path.with_suffix('.opus')
    else:
        output_path = Path(output_path)

    # Get original size
    original_size = get_file_size_mb(wav_path)

    print(f"Converting: {wav_path.name}")
    print(f"  Original size: {original_size:.1f} MB")

    # Build ffmpeg command
    # -y: overwrite output
    # -i: input file
    # -af loudnorm: normalize audio volume (important for low-volume recordings!)
    # -c:a libopus: use OPUS codec
    # -b:a: bitrate (48k is good for speech)
    # -ac 1: convert to mono (sufficient for speech)
    # -ar 48000: sample rate (OPUS standard)
    # -application voip: optimize for speech
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(wav_path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",  # Normalize audio to standard loudness
        "-c:a", "libopus",
        "-b:a", bitrate,
        "-ac", "1",
        "-ar", "48000",
        "-application", "voip",
        str(output_path)
    ]

    try:
        # Run ffmpeg with suppressed output
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )

        # Get new size
        new_size = get_file_size_mb(output_path)
        reduction = (1 - new_size / original_size) * 100

        print(f"  OPUS size: {new_size:.1f} MB")
        print(f"  Reduction: {reduction:.1f}%")
        print(f"  Saved: {output_path.name}")

        # Delete original if requested
        if delete_original:
            wav_path.unlink()
            print(f"  Deleted original: {wav_path.name}")

        return output_path

    except subprocess.CalledProcessError as e:
        print(f"  Error: ffmpeg conversion failed")
        print(f"  {e.stderr.decode()}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def convert_folder(folder_path, delete_originals=False):
    """Convert all WAV files in a folder to OPUS."""
    folder = Path(folder_path)
    wav_files = list(folder.glob("*.wav"))

    if not wav_files:
        print(f"No WAV files found in: {folder}")
        return

    print(f"Found {len(wav_files)} WAV file(s) to convert")
    print("=" * 60)

    total_original = 0
    total_new = 0
    converted = 0

    for i, wav_file in enumerate(wav_files, 1):
        print(f"\n[{i}/{len(wav_files)}]", end=" ")

        original_size = get_file_size_mb(wav_file)
        total_original += original_size

        output_path = convert_wav_to_opus(wav_file, delete_original=delete_originals)

        if output_path:
            total_new += get_file_size_mb(output_path)
            converted += 1

    # Summary
    print()
    print("=" * 60)
    print("CONVERSION COMPLETE")
    print("=" * 60)
    print(f"Files converted: {converted}/{len(wav_files)}")
    print(f"Original total: {total_original:.1f} MB")
    print(f"OPUS total: {total_new:.1f} MB")
    print(f"Space saved: {total_original - total_new:.1f} MB ({(1 - total_new/total_original)*100:.1f}%)")


def main():
    parser = argparse.ArgumentParser(
        description="Convert WAV files to OPUS for efficient storage"
    )
    parser.add_argument(
        'input',
        help='WAV file or folder containing WAV files'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path (for single file conversion)'
    )
    parser.add_argument(
        '--bitrate', '-b',
        default='48k',
        help='Audio bitrate (default: 48k, good for speech)'
    )
    parser.add_argument(
        '--delete', '-d',
        action='store_true',
        help='Delete original WAV files after conversion'
    )

    args = parser.parse_args()

    # Check ffmpeg
    if not check_ffmpeg():
        sys.exit(1)

    input_path = Path(args.input)

    if input_path.is_dir():
        convert_folder(input_path, delete_originals=args.delete)
    elif input_path.is_file():
        convert_wav_to_opus(
            input_path,
            output_path=args.output,
            bitrate=args.bitrate,
            delete_original=args.delete
        )
    else:
        print(f"Error: Path not found: {input_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
