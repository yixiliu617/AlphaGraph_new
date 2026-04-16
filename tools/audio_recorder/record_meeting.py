"""
Zoom Meeting Audio Recorder
Records system audio (what you hear) for meeting transcription.
Saves as OPUS by default (95% smaller than WAV).

Usage:
    python record_meeting.py                    # Start recording (press Ctrl+C to stop)
    python record_meeting.py --output my_meeting.opus
    python record_meeting.py --wav              # Save as WAV instead of OPUS
    python record_meeting.py --list-devices     # Show available audio devices

Requires ffmpeg for OPUS conversion:
    Windows: winget install ffmpeg
"""

import sys
import os
import argparse
import datetime
import subprocess
import shutil
import tempfile
import queue
from pathlib import Path

try:
    import sounddevice as sd
    import numpy as np
    from scipy.io import wavfile
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install sounddevice numpy scipy")
    sys.exit(1)


def check_ffmpeg():
    """Check if ffmpeg is installed."""
    return shutil.which("ffmpeg") is not None


def convert_wav_to_opus(wav_path, opus_path, delete_wav=True):
    """Convert WAV to OPUS using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(wav_path),
        "-c:a", "libopus",
        "-b:a", "48k",
        "-ac", "1",
        "-ar", "48000",
        "-application", "voip",
        str(opus_path)
    ]

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )

        if delete_wav:
            os.unlink(wav_path)

        return True
    except subprocess.CalledProcessError as e:
        print(f"OPUS conversion failed: {e.stderr.decode()}")
        return False


class AudioRecorder:
    def __init__(self, device=None, samplerate=44100, channels=2):
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.audio_queue = queue.Queue()
        self.recording = False
        self.frames = []

    def _audio_callback(self, indata, frames, time, status):
        """Called for each audio block during recording."""
        if status:
            print(f"Audio status: {status}", file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def _find_loopback_device(self):
        """Find a WASAPI loopback device for capturing system audio."""
        devices = sd.query_devices()

        # Look for loopback devices (Windows WASAPI)
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'loopback' in name or 'stereo mix' in name or 'what u hear' in name:
                if dev['max_input_channels'] > 0:
                    return i

        # If no loopback found, try to find a virtual audio cable
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'virtual' in name or 'cable' in name:
                if dev['max_input_channels'] > 0:
                    return i

        return None

    def start_recording(self, output_path, save_as_wav=False):
        """Start recording audio to the specified file."""
        if self.device is None:
            self.device = self._find_loopback_device()
            if self.device is not None:
                dev_info = sd.query_devices(self.device)
                print(f"Using loopback device: {dev_info['name']}")
            else:
                print("No loopback device found. Using default input device.")
                print("TIP: Enable 'Stereo Mix' in Windows Sound settings, or install VB-Audio Cable")
                self.device = sd.default.device[0]

        # Get device info
        try:
            dev_info = sd.query_devices(self.device)
            self.channels = min(self.channels, int(dev_info['max_input_channels']))
            if self.channels == 0:
                print(f"Error: Device '{dev_info['name']}' has no input channels")
                return False
            self.samplerate = int(dev_info['default_samplerate'])
        except Exception as e:
            print(f"Error querying device: {e}")
            return False

        # Check ffmpeg for OPUS
        use_opus = not save_as_wav
        if use_opus and not check_ffmpeg():
            print("WARNING: ffmpeg not found. Saving as WAV instead.")
            print("Install ffmpeg for OPUS support: winget install ffmpeg")
            use_opus = False

        output_path = Path(output_path)
        if use_opus:
            # Ensure .opus extension
            if output_path.suffix.lower() != '.opus':
                output_path = output_path.with_suffix('.opus')
            print(f"Format: OPUS (compressed)")
        else:
            # Ensure .wav extension
            if output_path.suffix.lower() != '.wav':
                output_path = output_path.with_suffix('.wav')
            print(f"Format: WAV (uncompressed)")

        print(f"Recording at {self.samplerate}Hz, {self.channels} channel(s)")
        print(f"Output: {output_path}")
        print("-" * 50)
        print("RECORDING... Press Ctrl+C to stop")
        print("-" * 50)

        self.recording = True
        self.frames = []

        try:
            with sd.InputStream(
                device=self.device,
                samplerate=self.samplerate,
                channels=self.channels,
                callback=self._audio_callback
            ):
                while self.recording:
                    try:
                        data = self.audio_queue.get(timeout=0.1)
                        self.frames.append(data)
                    except queue.Empty:
                        pass
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"Recording error: {e}")
            return False

        return self._save_recording(output_path, use_opus)

    def stop_recording(self):
        """Stop the current recording."""
        self.recording = False

    def _save_recording(self, output_path, use_opus=True):
        """Save recorded audio to file."""
        if not self.frames:
            print("No audio recorded!")
            return False

        print("\nProcessing recording...")
        audio_data = np.concatenate(self.frames, axis=0)
        duration = len(audio_data) / self.samplerate

        # Convert to int16
        audio_int16 = np.int16(audio_data * 32767)

        output_path = Path(output_path)

        if use_opus:
            # Save to temp WAV first, then convert to OPUS
            temp_wav = output_path.with_suffix('.temp.wav')
            wavfile.write(str(temp_wav), self.samplerate, audio_int16)
            wav_size = os.path.getsize(temp_wav) / (1024 * 1024)

            print(f"Converting to OPUS...")
            if convert_wav_to_opus(temp_wav, output_path, delete_wav=True):
                opus_size = os.path.getsize(output_path) / (1024 * 1024)
                reduction = (1 - opus_size / wav_size) * 100

                print(f"Saved: {output_path}")
                print(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                print(f"File size: {opus_size:.1f} MB (reduced {reduction:.0f}% from {wav_size:.1f} MB)")
                return True
            else:
                # Fallback to WAV if OPUS conversion fails
                print("OPUS conversion failed. Saving as WAV...")
                wav_path = output_path.with_suffix('.wav')
                if temp_wav.exists():
                    temp_wav.rename(wav_path)
                else:
                    wavfile.write(str(wav_path), self.samplerate, audio_int16)
                print(f"Saved: {wav_path}")
                print(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                print(f"File size: {wav_size:.1f} MB")
                return True
        else:
            # Save as WAV
            wavfile.write(str(output_path), self.samplerate, audio_int16)
            file_size = os.path.getsize(output_path) / (1024 * 1024)

            print(f"Saved: {output_path}")
            print(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
            print(f"File size: {file_size:.1f} MB")
            return True


def list_devices():
    """List all available audio devices."""
    print("Available Audio Devices:")
    print("=" * 60)
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        direction = ""
        if dev['max_input_channels'] > 0:
            direction += "IN"
        if dev['max_output_channels'] > 0:
            direction += "/OUT" if direction else "OUT"

        marker = ""
        name_lower = dev['name'].lower()
        if 'loopback' in name_lower or 'stereo mix' in name_lower:
            marker = " [LOOPBACK]"

        print(f"  [{i}] {dev['name']} ({direction}){marker}")
    print()
    print("TIP: For recording Zoom audio, use a loopback device or Stereo Mix")


def generate_output_filename(extension='opus'):
    """Generate a timestamped output filename."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"meeting_recording_{timestamp}.{extension}"


def main():
    parser = argparse.ArgumentParser(
        description="Record system audio from Zoom meetings (saves as OPUS by default)"
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path (default: auto-generated timestamp)'
    )
    parser.add_argument(
        '--device', '-d',
        type=int,
        help='Audio device index (use --list-devices to see options)'
    )
    parser.add_argument(
        '--wav', '-w',
        action='store_true',
        help='Save as WAV instead of OPUS (larger file)'
    )
    parser.add_argument(
        '--list-devices', '-l',
        action='store_true',
        help='List available audio devices and exit'
    )

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    # Set output path
    ext = 'wav' if args.wav else 'opus'
    if args.output:
        output_path = args.output
    else:
        # Create recordings directory
        recordings_dir = Path(__file__).parent / "recordings"
        recordings_dir.mkdir(exist_ok=True)
        output_path = str(recordings_dir / generate_output_filename(ext))

    # Start recording
    recorder = AudioRecorder(device=args.device)
    success = recorder.start_recording(output_path, save_as_wav=args.wav)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
