"""Parse SenseVoice output from meeting recording and save results."""
import re
import json
from funasr import AutoModel

model = AutoModel(
    model="iic/SenseVoiceSmall",
    vad_model="fsmn-vad",
    vad_kwargs={"max_single_segment_time": 30000},
    device="cpu",
    disable_update=True,
)

audio_file = "tools/audio_recorder/recordings/meeting_recording_20260410_110453.opus"
result = model.generate(input=audio_file, cache={}, language="auto", use_itn=True, batch_size_s=60)

text = result[0].get("text", "")
segments = re.findall(
    r'<\|([^|]+)\|><\|([^|]+)\|><\|([^|]+)\|><\|([^|]+)\|>(.*?)(?=<\||$)', text
)

# Count languages
langs = {}
for lang, emotion, event, extra, content in segments:
    langs[lang] = langs.get(lang, 0) + 1

# Build structured output
parsed = []
for lang, emotion, event, extra, content in segments:
    parsed.append({
        "lang": lang,
        "emotion": emotion,
        "event": event,
        "text": content.strip(),
    })

# Save as JSON
with open("tools/audio_recorder/recordings/meeting_sensevoice.json", "w", encoding="utf-8") as f:
    json.dump({"total_segments": len(segments), "languages": langs, "segments": parsed}, f, ensure_ascii=False, indent=2)

# Save clean text
clean = re.sub(r"<\|[^|]+\|>", "", text)
with open("tools/audio_recorder/recordings/meeting_sensevoice.txt", "w", encoding="utf-8") as f:
    f.write(clean)

print(f"Total segments: {len(segments)}")
print(f"Languages: {langs}")
print(f"Clean text length: {len(clean)} chars")
print(f"Saved: meeting_sensevoice.json + meeting_sensevoice.txt")

# Print first 10 segments as ASCII-safe
for i, seg in enumerate(parsed[:10]):
    t = seg["text"].encode("ascii", "replace").decode()[:80]
    print(f"  [{i+1}] [{seg['lang']}|{seg['emotion']}] {t}")
