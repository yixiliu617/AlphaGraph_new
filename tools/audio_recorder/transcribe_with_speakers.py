"""
Post-Meeting Transcription with Speaker Identification (Deepgram)
Transcribes audio recordings with speaker labels using Deepgram API.
Supports WAV, OPUS, MP3, and other common formats.

Usage:
    python transcribe_with_speakers.py recording.opus
    python transcribe_with_speakers.py recording.opus --language zh  # Chinese
    python transcribe_with_speakers.py recording.opus --language en  # English
    python transcribe_with_speakers.py recordings    (all audio files in folder)

Cost: ~$0.007/min (includes diarization)

Requires:
    - DEEPGRAM_API_KEY in .env file
    - pip install deepgram-sdk python-dotenv
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
    from deepgram import DeepgramClient
    from deepgram.core.request_options import RequestOptions
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with:")
    print("  pip install deepgram-sdk python-dotenv")
    sys.exit(1)

load_dotenv()


def format_timestamp(seconds):
    """Convert seconds to MM:SS or HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def transcribe_file(audio_path, output_path=None, language=None):
    """Transcribe a single audio file with speaker diarization."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("ERROR: DEEPGRAM_API_KEY not found in .env file")
        sys.exit(1)

    audio_path = Path(audio_path)
    if not audio_path.exists():
        print(f"Error: File not found: {audio_path}")
        return False

    # Get file size
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

    print(f"Transcribing with speaker identification: {audio_path.name}")
    print(f"File size: {file_size_mb:.1f} MB")
    if language:
        print(f"Language: {language}")
    else:
        print("Language: auto-detect")
    print("-" * 60)

    # Initialize Deepgram
    deepgram = DeepgramClient(api_key=api_key)

    # Read audio file
    with open(audio_path, "rb") as f:
        audio_data = f.read()

    # Transcribe with extended timeout for large files
    if file_size_mb > 50:
        print(f"Uploading {file_size_mb:.0f} MB (large file, may take a few minutes)...")
    else:
        print("Uploading and processing...")

    # Build transcription options
    transcribe_options = {
        "request": audio_data,
        "model": "nova-2",
        "smart_format": True,
        "diarize": True,
        "punctuate": True,
        "paragraphs": True,
        "utterances": True,
        "request_options": RequestOptions(timeout_in_seconds=600),
    }

    # Set language (explicit or auto-detect)
    if language:
        transcribe_options["language"] = language
    else:
        transcribe_options["detect_language"] = True

    response = deepgram.listen.v1.media.transcribe_file(**transcribe_options)

    # Get metadata
    duration = 0
    if hasattr(response, 'metadata') and response.metadata:
        duration = getattr(response.metadata, 'duration', 0)
        detected_lang = getattr(response.metadata, 'language', 'unknown')
        print(f"Detected language: {detected_lang}")
        print(f"Audio duration: {duration/60:.1f} minutes")

    # Get results
    results = response.results if hasattr(response, 'results') else response

    # Get utterances
    utterances = []
    if hasattr(results, 'utterances') and results.utterances:
        utterances = results.utterances

    # Get words as fallback for duration
    words = []
    if hasattr(results, 'channels') and results.channels:
        channel = results.channels[0]
        if hasattr(channel, 'alternatives') and channel.alternatives:
            alt = channel.alternatives[0]
            if hasattr(alt, 'words') and alt.words:
                words = alt.words

    # Use word duration if metadata duration not available
    if duration == 0 and words:
        duration = words[-1].end if hasattr(words[-1], 'end') else 0

    print(f"Utterances found: {len(utterances)}")
    print(f"Words found: {len(words)}")
    print("-" * 60)
    print()

    # Build transcript from utterances
    transcript_lines = []
    speaker_map = {}

    if utterances:
        for utt in utterances:
            speaker_id = utt.speaker if hasattr(utt, 'speaker') else 0
            start = utt.start if hasattr(utt, 'start') else 0
            text = utt.transcript if hasattr(utt, 'transcript') else str(utt)

            if speaker_id not in speaker_map:
                speaker_map[speaker_id] = f"Speaker {chr(65 + len(speaker_map))}"

            speaker_name = speaker_map[speaker_id]
            timestamp = format_timestamp(start)
            text_clean = text.strip() if text else ""

            if text_clean:  # Only add non-empty lines
                line = f"[{timestamp}] {speaker_name}: {text_clean}"
                print(line)
                transcript_lines.append(line)

    elif words:
        # Fallback: group words by speaker
        current_speaker = None
        current_start = 0
        current_text = []

        for word in words:
            speaker_id = word.speaker if hasattr(word, 'speaker') else 0
            word_text = word.punctuated_word if hasattr(word, 'punctuated_word') else (word.word if hasattr(word, 'word') else '')
            word_start = word.start if hasattr(word, 'start') else 0

            if speaker_id != current_speaker:
                if current_text:
                    if current_speaker not in speaker_map:
                        speaker_map[current_speaker] = f"Speaker {chr(65 + len(speaker_map))}"
                    speaker_name = speaker_map[current_speaker]
                    timestamp = format_timestamp(current_start)
                    line = f"[{timestamp}] {speaker_name}: {' '.join(current_text)}"
                    print(line)
                    transcript_lines.append(line)

                current_speaker = speaker_id
                current_start = word_start
                current_text = [word_text] if word_text else []
            else:
                if word_text:
                    current_text.append(word_text)

        # Save last segment
        if current_text:
            if current_speaker not in speaker_map:
                speaker_map[current_speaker] = f"Speaker {chr(65 + len(speaker_map))}"
            speaker_name = speaker_map[current_speaker]
            timestamp = format_timestamp(current_start)
            line = f"[{timestamp}] {speaker_name}: {' '.join(current_text)}"
            print(line)
            transcript_lines.append(line)

    if not transcript_lines:
        print("Warning: No transcript generated. Check audio quality or try specifying --language")
        return False

    # Determine output path
    if output_path is None:
        transcripts_dir = Path(__file__).parent / "transcripts"
        transcripts_dir.mkdir(exist_ok=True)
        output_path = transcripts_dir / f"{audio_path.stem}_speakers.txt"

    # Save transcript
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"Transcript: {audio_path.name}\n")
        f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Duration: {duration/60:.1f} minutes\n")
        f.write(f"Speakers detected: {len(speaker_map)}\n")
        f.write("=" * 60 + "\n\n")

        f.write("## Speakers\n")
        for sid, name in sorted(speaker_map.items()):
            f.write(f"  {name} (ID: {sid})\n")
        f.write("\n")

        f.write("## Transcript\n\n")
        for line in transcript_lines:
            f.write(line + "\n")

    print()
    print("-" * 60)
    print(f"Speakers detected: {len(speaker_map)}")
    print(f"Transcript saved: {output_path}")

    return True


def transcribe_folder(folder_path, language=None):
    """Transcribe all audio files in a folder."""
    folder = Path(folder_path)

    # Find all supported audio files
    audio_files = []
    for ext in ['*.opus', '*.wav', '*.mp3', '*.m4a', '*.ogg']:
        audio_files.extend(folder.glob(ext))

    if not audio_files:
        print(f"No audio files found in: {folder}")
        return

    # Sort by name
    audio_files = sorted(audio_files, key=lambda x: x.name)

    print(f"Found {len(audio_files)} audio file(s) to transcribe")

    for i, audio_file in enumerate(audio_files, 1):
        print(f"\n[{i}/{len(audio_files)}] {audio_file.name}")
        print("=" * 60)

        transcript_path = Path(__file__).parent / "transcripts" / f"{audio_file.stem}_speakers.txt"
        if transcript_path.exists():
            print(f"Already transcribed. Delete {transcript_path.name} to re-process.")
            continue

        transcribe_file(audio_file, language=language)

    print("\n" + "=" * 60)
    print("All transcriptions complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe recordings with speaker identification (Deepgram)"
    )
    parser.add_argument(
        'input',
        help='Audio file (WAV/OPUS/MP3) or folder containing audio files'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output transcript file path'
    )
    parser.add_argument(
        '--language', '-l',
        help='Language code (e.g., "zh" for Chinese, "en" for English). Default: auto-detect'
    )

    args = parser.parse_args()
    input_path = Path(args.input)

    if input_path.is_dir():
        transcribe_folder(input_path, language=args.language)
    elif input_path.is_file():
        transcribe_file(input_path, args.output, language=args.language)
    else:
        print(f"Error: Path not found: {input_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
