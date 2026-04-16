"""Quick test to check WAV transcription vs OPUS"""
import os
import sys
from pathlib import Path

# Add current dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from deepgram import DeepgramClient
from deepgram.core.request_options import RequestOptions
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv('DEEPGRAM_API_KEY')

if not api_key:
    print("ERROR: No DEEPGRAM_API_KEY found")
    sys.exit(1)

deepgram = DeepgramClient(api_key=api_key)

wav_path = Path(__file__).parent / 'recordings' / 'meeting_recording_20260410_110453.wav'
print(f'File: {wav_path.name}')
print(f'Size: {os.path.getsize(wav_path)/1024/1024:.1f} MB')

print('Reading file...')
with open(wav_path, 'rb') as f:
    audio_data = f.read()

print(f'Uploading {len(audio_data)/1024/1024:.1f} MB to Deepgram...')
print('This will take a few minutes for a large file...')

response = deepgram.listen.v1.media.transcribe_file(
    request=audio_data,
    model='nova-2',
    language='en',
    smart_format=True,
    diarize=True,
    punctuate=True,
    utterances=True,
    request_options=RequestOptions(timeout_in_seconds=900),
)

# Parse response
result = response.results if hasattr(response, 'results') else response

# Get metadata
metadata = response.metadata if hasattr(response, 'metadata') else None
if metadata:
    print(f'Deepgram duration: {metadata.duration:.1f} seconds ({metadata.duration/60:.1f} min)')

# Count utterances
utterances = result.utterances if hasattr(result, 'utterances') else []
print(f'Utterances: {len(utterances)}')

# Count words
channels = result.channels if hasattr(result, 'channels') else []
if channels:
    words = channels[0].alternatives[0].words if channels[0].alternatives else []
    print(f'Words: {len(words)}')
    if words:
        print(f'Audio duration (from words): {words[-1].end/60:.1f} min')

# Show first few utterances
print('\nFirst 10 utterances:')
for i, utt in enumerate(utterances[:10]):
    speaker = utt.speaker if hasattr(utt, 'speaker') else '?'
    text = utt.transcript if hasattr(utt, 'transcript') else str(utt)
    start = utt.start if hasattr(utt, 'start') else 0
    print(f'  [{start/60:.1f}m] Speaker {speaker}: {text[:60]}...')
