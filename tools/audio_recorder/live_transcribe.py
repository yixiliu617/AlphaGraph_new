"""
Live Meeting Transcription using Deepgram
Streams system audio to Deepgram for real-time transcription.

Usage:
    python live_transcribe.py
    python live_transcribe.py --output transcript.txt

Requires:
    - DEEPGRAM_API_KEY environment variable or .env file
    - pip install deepgram-sdk sounddevice python-dotenv
"""

import sys
import os
import argparse
import asyncio
import datetime
import threading
import queue
from pathlib import Path

try:
    import sounddevice as sd
    import numpy as np
    from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with:")
    print("  pip install deepgram-sdk sounddevice numpy python-dotenv")
    sys.exit(1)

# Load environment variables
load_dotenv()


class LiveTranscriber:
    def __init__(self, output_file=None):
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            print("ERROR: DEEPGRAM_API_KEY not found!")
            print("Set it with: set DEEPGRAM_API_KEY=your_key_here")
            print("Or create a .env file with: DEEPGRAM_API_KEY=your_key_here")
            print("\nGet free API key at: https://console.deepgram.com/signup")
            sys.exit(1)

        self.output_file = output_file
        self.transcript_lines = []
        self.audio_queue = queue.Queue()
        self.running = False
        self.samplerate = 16000  # Deepgram prefers 16kHz
        self.channels = 1

    def _find_loopback_device(self):
        """Find a WASAPI loopback device for capturing system audio."""
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'loopback' in name or 'stereo mix' in name or 'what u hear' in name:
                if dev['max_input_channels'] > 0:
                    return i
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'virtual' in name or 'cable' in name:
                if dev['max_input_channels'] > 0:
                    return i
        return None

    def _audio_callback(self, indata, frames, time, status):
        """Called for each audio block."""
        if status:
            print(f"Audio status: {status}", file=sys.stderr)
        # Convert to mono if stereo, and to int16
        if indata.shape[1] > 1:
            mono = np.mean(indata, axis=1)
        else:
            mono = indata[:, 0]
        audio_bytes = (mono * 32767).astype(np.int16).tobytes()
        self.audio_queue.put(audio_bytes)

    async def start(self):
        """Start live transcription."""
        # Find audio device
        device = self._find_loopback_device()
        if device is not None:
            dev_info = sd.query_devices(device)
            print(f"Using audio device: {dev_info['name']}")
            input_channels = int(dev_info['max_input_channels'])
        else:
            print("No loopback device found. Using default input.")
            print("TIP: Enable 'Stereo Mix' in Windows Sound settings")
            device = sd.default.device[0]
            dev_info = sd.query_devices(device)
            input_channels = int(dev_info['max_input_channels'])

        # Initialize Deepgram
        deepgram = DeepgramClient(self.api_key)
        dg_connection = deepgram.listen.live.v("1")

        # Set up event handlers
        def on_message(self_handler, result, **kwargs):
            transcript = result.channel.alternatives[0].transcript
            if transcript.strip():
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                line = f"[{timestamp}] {transcript}"
                print(line)
                self.transcript_lines.append(line)

        def on_error(self_handler, error, **kwargs):
            print(f"Deepgram error: {error}")

        def on_close(self_handler, close, **kwargs):
            print("\nDeepgram connection closed")

        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Close, on_close)

        # Configure Deepgram
        options = LiveOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            encoding="linear16",
            sample_rate=self.samplerate,
            channels=1,
        )

        # Start Deepgram connection
        if not dg_connection.start(options):
            print("Failed to connect to Deepgram")
            return

        print("=" * 60)
        print("LIVE TRANSCRIPTION ACTIVE")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        print()

        self.running = True

        # Start audio capture in separate thread
        def audio_sender():
            while self.running:
                try:
                    audio_data = self.audio_queue.get(timeout=0.1)
                    dg_connection.send(audio_data)
                except queue.Empty:
                    pass
                except Exception as e:
                    if self.running:
                        print(f"Send error: {e}")

        sender_thread = threading.Thread(target=audio_sender, daemon=True)
        sender_thread.start()

        try:
            with sd.InputStream(
                device=device,
                samplerate=self.samplerate,
                channels=input_channels,
                callback=self._audio_callback,
                blocksize=int(self.samplerate * 0.1),  # 100ms blocks
            ):
                while self.running:
                    await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            dg_connection.finish()

        # Save transcript
        self._save_transcript()

    def _save_transcript(self):
        """Save transcript to file."""
        if not self.transcript_lines:
            print("\nNo transcript to save.")
            return

        if self.output_file:
            output_path = self.output_file
        else:
            transcripts_dir = Path(__file__).parent / "transcripts"
            transcripts_dir.mkdir(exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = transcripts_dir / f"live_transcript_{timestamp}.txt"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"Live Transcript - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write("=" * 60 + "\n\n")
            for line in self.transcript_lines:
                f.write(line + "\n")

        print(f"\nTranscript saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Live meeting transcription with Deepgram")
    parser.add_argument('--output', '-o', help='Output transcript file path')
    args = parser.parse_args()

    transcriber = LiveTranscriber(output_file=args.output)
    asyncio.run(transcriber.start())


if __name__ == "__main__":
    main()
