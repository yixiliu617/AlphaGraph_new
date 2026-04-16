"""Debug script to investigate Deepgram response"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from deepgram import DeepgramClient
from deepgram.core.request_options import RequestOptions

load_dotenv()
api_key = os.getenv('DEEPGRAM_API_KEY')

if not api_key:
    print("ERROR: No DEEPGRAM_API_KEY")
    sys.exit(1)

opus_path = Path(__file__).parent / 'recordings' / 'meeting_recording_20260410_110453.opus'
print(f"File: {opus_path.name}")
print(f"Size: {os.path.getsize(opus_path)/1024/1024:.1f} MB")

print("\nReading file...")
with open(opus_path, 'rb') as f:
    audio_data = f.read()

print(f"Audio data size: {len(audio_data)} bytes")

deepgram = DeepgramClient(api_key=api_key)

print("\nSending to Deepgram with language=zh (Chinese)...")
response = deepgram.listen.v1.media.transcribe_file(
    request=audio_data,
    model="nova-2",
    language="zh",  # Force Chinese
    smart_format=True,
    diarize=True,
    punctuate=True,
    utterances=True,
    request_options=RequestOptions(timeout_in_seconds=600),
)

# Debug: Print response structure
print("\n=== RESPONSE DEBUG ===")
print(f"Response type: {type(response)}")

# Check for metadata
if hasattr(response, 'metadata'):
    meta = response.metadata
    print(f"\nMetadata:")
    print(f"  Duration: {getattr(meta, 'duration', 'N/A')} seconds")
    print(f"  Channels: {getattr(meta, 'channels', 'N/A')}")
    print(f"  Model: {getattr(meta, 'model_info', 'N/A')}")

# Check results
if hasattr(response, 'results'):
    results = response.results

    # Check utterances
    if hasattr(results, 'utterances') and results.utterances:
        print(f"\nUtterances: {len(results.utterances)}")
        print("First 5 utterances:")
        for i, utt in enumerate(results.utterances[:5]):
            print(f"  [{utt.start:.1f}s] Speaker {utt.speaker}: {utt.transcript[:50]}...")
        print("Last 5 utterances:")
        for utt in results.utterances[-5:]:
            print(f"  [{utt.start:.1f}s] Speaker {utt.speaker}: {utt.transcript[:50]}...")

    # Check channels/words
    if hasattr(results, 'channels') and results.channels:
        channel = results.channels[0]
        if hasattr(channel, 'alternatives') and channel.alternatives:
            alt = channel.alternatives[0]
            if hasattr(alt, 'words') and alt.words:
                words = alt.words
                print(f"\nTotal words: {len(words)}")
                if words:
                    print(f"First word: {words[0].word} at {words[0].start}s")
                    print(f"Last word: {words[-1].word} at {words[-1].end}s")
                    print(f"Audio duration from words: {words[-1].end:.1f} seconds ({words[-1].end/60:.1f} min)")

            # Full transcript
            if hasattr(alt, 'transcript'):
                transcript = alt.transcript
                print(f"\nFull transcript length: {len(transcript)} chars")
                print(f"First 200 chars: {transcript[:200]}...")
                print(f"Last 200 chars: ...{transcript[-200:]}")

print("\n=== END DEBUG ===")
