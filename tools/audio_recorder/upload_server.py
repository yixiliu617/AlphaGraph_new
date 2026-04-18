"""
upload_server.py -- Local WiFi upload server for iPhone screen recordings.

Runs on your PC. The iOS Shortcut sends the recording directly to this
server over your local network — no cloud, no sync delay, instant transfer.

Usage:
    python upload_server.py                    # Start on port 8899
    python upload_server.py --port 9000        # Custom port
    python upload_server.py --auto-process     # Auto-extract audio + transcribe

The server accepts POST requests with a file attachment and saves it to
the recordings/ folder. After saving, optionally runs extract_audio.py
and the transcription pipeline.

iOS Shortcut setup:
    1. After "Stop Screen Recording", add action "Get Latest Screen Recording"
    2. Add action "Get File from URL" (or "Upload File"):
       - URL: http://<your-PC-local-IP>:8899/upload
       - Method: POST
       - Request Body: File (the screen recording)
    3. Done — the file transfers over WiFi in seconds.

Find your PC's local IP:
    Windows: ipconfig | findstr IPv4
    Usually something like 192.168.1.xxx or 10.0.0.xxx
"""
import os
import sys
import argparse
import socket
import datetime
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = SCRIPT_DIR / "recordings"
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB


class UploadHandler(BaseHTTPRequestHandler):
    auto_process = False

    def do_POST(self):
        if self.path != "/upload":
            self.send_error(404, "Use POST /upload")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_SIZE:
            self.send_error(413, "File too large (max 2 GB)")
            return
        if content_length == 0:
            self.send_error(400, "No file data received")
            return

        # Generate filename from timestamp
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Try to detect extension from Content-Type
        ct = self.headers.get("Content-Type", "")
        if "mp4" in ct or "video/mp4" in ct:
            ext = ".mp4"
        elif "quicktime" in ct or "mov" in ct:
            ext = ".mov"
        elif "m4a" in ct:
            ext = ".m4a"
        else:
            ext = ".mp4"  # default for screen recordings

        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"iphone_recording_{ts}{ext}"
        filepath = RECORDINGS_DIR / filename

        # Read and save
        print(f"Receiving: {filename} ({content_length / (1024*1024):.1f} MB)...")
        with open(filepath, "wb") as f:
            remaining = content_length
            while remaining > 0:
                chunk_size = min(65536, remaining)
                chunk = self.rfile.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)

        actual_size = filepath.stat().st_size
        print(f"Saved: {filepath} ({actual_size / (1024*1024):.1f} MB)")

        # Respond to the iPhone immediately so the Shortcut doesn't hang
        response = f'{{"status": "ok", "filename": "{filename}", "size_mb": {actual_size / (1024*1024):.1f}}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response.encode())

        # Auto-process in background (after response is sent)
        if self.auto_process:
            self._process(filepath)

    def _process(self, filepath: Path):
        """Extract audio and optionally transcribe."""
        try:
            print(f"Auto-processing: {filepath.name}")
            extract_script = SCRIPT_DIR / "extract_audio.py"
            result = subprocess.run(
                [sys.executable, str(extract_script), str(filepath)],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                opus_path = filepath.with_suffix(".opus")
                print(f"Audio extracted: {opus_path.name}")
                # Optionally trigger transcription here
                # transcribe_script = SCRIPT_DIR / "transcribe_with_speakers.py"
                # subprocess.run([sys.executable, str(transcribe_script), str(opus_path)])
            else:
                print(f"Extract failed: {result.stderr[:200]}")
        except Exception as e:
            print(f"Auto-process error: {e}")

    def do_GET(self):
        """Health check / landing page."""
        if self.path == "/":
            body = (
                "AlphaGraph iPhone Upload Server\n\n"
                "POST /upload  - Upload a screen recording\n"
                f"Recordings saved to: {RECORDINGS_DIR}\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        # Cleaner log format
        print(f"[{self.log_date_time_string()}] {format % args}")


def get_local_ip():
    """Get the machine's local network IP (what iPhone would connect to)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description="Local WiFi upload server for iPhone recordings")
    parser.add_argument("--port", type=int, default=8899, help="Port to listen on (default: 8899)")
    parser.add_argument("--auto-process", action="store_true",
                        help="Auto-extract audio to OPUS after upload")
    args = parser.parse_args()

    UploadHandler.auto_process = args.auto_process
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    local_ip = get_local_ip()
    server = HTTPServer(("0.0.0.0", args.port), UploadHandler)

    print(f"Upload server running on port {args.port}")
    print(f"")
    print(f"Your PC's local IP: {local_ip}")
    print(f"")
    print(f"iOS Shortcut URL:  http://{local_ip}:{args.port}/upload")
    print(f"")
    print(f"Recordings will be saved to: {RECORDINGS_DIR}")
    print(f"Auto-process: {'ON' if args.auto_process else 'OFF'}")
    print(f"")
    print(f"Press Ctrl+C to stop.")
    print(f"")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
