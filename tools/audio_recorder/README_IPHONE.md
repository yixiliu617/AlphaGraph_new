# iPhone Meeting Recording Setup

Record Zoom/Webex/Teams meetings on your iPhone and auto-feed them into AlphaGraph's transcription pipeline.

## How it works

iOS Screen Recording captures **system audio** (both sides of the call — what you hear AND your mic input). This is the key insight: unlike Voice Memos, Screen Recording is a system-level feature that can capture app audio directly.

```
iPhone                                    PC
──────                                    ──
1. Open Zoom/Webex                        
2. Screen Recording starts (auto)         
3. Meeting happens                        
4. Screen Recording stops (auto)          
5. File saves to Camera Roll              
6. iCloud syncs .mp4 to PC folder    →    7. watch_folder.py detects new .mp4
                                          8. extract_audio.py strips video → .opus
                                          9. transcribe_recording.py → transcript
```

## One-time setup

### Step 1 — Enable Screen Recording in Control Center

1. Settings → Control Center
2. Add "Screen Recording" if not already there

### Step 2 — Enable mic capture for Screen Recording

1. Swipe down to open Control Center
2. **Long-press** the Screen Recording button (circle icon)
3. Toggle **Microphone ON** at the bottom
4. This setting is sticky — only needs to be set once

### Step 3 — Set up iOS Shortcuts automation (optional but recommended)

Open the **Shortcuts** app → **Automation** tab → **+** → **Create Personal Automation**

**Start recording when Zoom opens:**
1. Trigger: "App" → select "Zoom" (or "Webex") → "Is Opened"
2. Action: "Start Recording Screen" (from the Screen Recording actions)
3. Toggle OFF "Ask Before Running"
4. Save

**Stop recording when Zoom closes:**
1. Trigger: "App" → select "Zoom" (or "Webex") → "Is Closed"
2. Action: "Stop Recording Screen"
3. Toggle OFF "Ask Before Running"
4. Save

> **Note**: iOS may ask for confirmation the first few times. After that it runs silently.

### Step 4 — Set up auto-sync to PC

**Option A — iCloud Drive (recommended):**
1. On iPhone: Settings → Photos → iCloud Photos → ON
2. On PC: Install iCloud for Windows, enable iCloud Photos sync
3. Screen recordings sync to `C:\Users\{you}\iCloud Photos\` automatically

**Option B — OneDrive:**
1. Install OneDrive on iPhone
2. Settings → Camera Upload → ON
3. Files sync to `C:\Users\{you}\OneDrive\Pictures\Camera Roll\`

**Option C — Manual AirDrop:**
1. After meeting, open Camera Roll
2. Share → AirDrop → your PC
3. Saves to Downloads folder

### Step 5 — Start the folder watcher on PC

```powershell
# Watch iCloud sync folder, auto-transcribe with speaker diarization
python tools/audio_recorder/watch_folder.py "C:\Users\Sharo\iCloud Photos" --speakers

# Or: watch OneDrive, local Whisper transcription
python tools/audio_recorder/watch_folder.py "C:\Users\Sharo\OneDrive\Pictures\Camera Roll" --transcribe

# Or: one-shot (process what's there, then exit)
python tools/audio_recorder/watch_folder.py ./incoming --transcribe --once
```

## Manual workflow (without folder watcher)

```powershell
# Extract audio from a screen recording
python tools/audio_recorder/extract_audio.py recording.mp4

# Transcribe (local, no speakers)
python tools/audio_recorder/transcribe_recording.py recording.opus

# Transcribe (cloud, with speaker diarization)
python tools/audio_recorder/transcribe_with_speakers.py recording.opus
```

## Size efficiency

| What | 1-hour meeting |
|---|---|
| iPhone screen recording (raw .mp4) | 500 MB – 1 GB |
| Extracted audio (.opus, 48 kbps) | **~22 MB** |
| Full transcript (.txt) | ~30 KB |

The video track is discarded immediately during extraction. Only the 22 MB audio file is kept.

## What gets recorded

| Audio source | Captured? | How |
|---|---|---|
| Remote participants (their audio) | Yes | System audio capture |
| Your voice (your mic) | Yes, if mic toggled ON in Screen Recording | Mic overlay |
| Notification sounds | Yes | System audio (mute notifications during meetings) |
| Other app audio | Yes | System audio (close other apps playing audio) |

## Tips

- **Mute notifications** during meetings (Focus mode → Do Not Disturb) to avoid notification sounds in the recording.
- **Close other audio apps** (music, podcasts) before starting.
- **The red status bar** at the top of iPhone is visible to you but NOT to other meeting participants.
- **No notification** is sent to other participants that you're recording.
- **Battery**: Screen recording uses ~5-10% battery per hour. Keep iPhone charged for long meetings.
- **Storage**: iPhone needs enough free space for the raw recording (~500 MB/hr). It syncs and can be deleted after.

## Troubleshooting

**"Screen Recording failed to save"**: iPhone is low on storage. Free up space.

**No audio in extracted file**: Mic was OFF in Screen Recording. Long-press the Screen Recording icon in Control Center → toggle mic ON.

**Only my voice, not the other side**: The meeting app might be using the earpiece speaker (not the main speaker). Put the call on speaker mode, or use headphones (audio will be captured either way via Screen Recording's system audio path).

**File not syncing to PC**: Check iCloud/OneDrive sync status. Large files can take 5-10 minutes to upload on slow connections.
