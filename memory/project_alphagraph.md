# AlphaGraph Project Status

## Current State (2026-04-10)

### Completed Components

#### 1. Core Extraction Pipeline
- PDF ingestion with PyMuPDF
- Parallel extraction via `run_parallel_extraction.py`
- Data fragment storage in SQLite (alphagraph.db)

#### 2. Extraction Modules (in `backend/scripts/extractors/`)
- `causal_extractor.py` - Cause-effect relationship chains
- `chart_extractor.py` - Chart images + vision LLM analysis
- `company_intel_extractor.py` - Business segments, metrics, products
- `relationship_extractor.py` - Inter-company links (supplies, competes, partners)
- `doc_metadata.py` - Document metadata extraction (shared by all modules)

#### 3. Frontend Tabs (in `frontend/src/app/(dashboard)/`)
- `topology/` - Knowledge graph visualization
- `engine/` - Extraction pipeline control
- `library/` - Document management
- `synthesis/` - AI-generated insights
- `monitors/` - System monitoring
- `settings/` - Configuration

#### 3. Audio Capture & Transcription (NEW)
- **Location**: `tools/audio_recorder/`
- **Status**: Functional, tested with 30-min Chinese meeting
- **Components**:
  - Recording: sounddevice + WASAPI loopback
  - Conversion: ffmpeg + loudnorm normalization
  - Transcription: Local Whisper (free) + Deepgram (speaker ID)
- **Pending**: Frontend Notes tab integration, AI summarization

### Environment Setup

```bash
# Backend
cd backend
pip install -r requirements.txt

# Audio Recorder
cd tools/audio_recorder
pip install -r requirements.txt
# Requires: ffmpeg (winget install ffmpeg)
# Requires: DEEPGRAM_API_KEY in .env for speaker identification

# Frontend
cd frontend
npm install
```

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Audio format | OPUS | 95% smaller than WAV, widely supported |
| Local transcription | faster-whisper | Free, runs on CPU, auto language detection |
| Cloud transcription | Deepgram nova-2 | Speaker diarization, fast, ~$0.007/min |
| Volume normalization | loudnorm filter | Required for low-volume recordings |

### Known Issues / Constraints

1. **Deepgram language**: Must specify `--language zh` for Chinese meetings
2. **Large WAV uploads**: Timeout on files >100MB, use OPUS instead
3. **Local Whisper on CPU**: Slower than GPU, but accurate with base/medium models

---

## Next Steps / Backlog

### High Priority
- [ ] Notes tab frontend (Container/View pattern)
- [ ] AI summarization of transcripts (key points, action items)
- [ ] Metadata extraction (speakers, topics, timestamps)
- [ ] Transcript search/indexing

### Medium Priority
- [ ] Live transcription display during recording
- [ ] Speaker name assignment (replace Speaker A/B with actual names)
- [ ] Integration with main AlphaGraph knowledge graph

### Low Priority
- [ ] Multi-language support in single meeting
- [ ] Automatic meeting detection (start/stop recording)
- [ ] Cloud storage for recordings

---

## Architecture Notes

See `architecture_and_design_v2.md` Section 11 for audio subsystem design.

The audio system is intentionally decoupled from the core extraction pipeline:
- Lives in `tools/` not `backend/app/services/`
- No shared database tables (yet)
- Will integrate via new `/api/v1/notes` endpoints when frontend is built
