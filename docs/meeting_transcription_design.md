# Meeting Transcription System — Design Document

**Date:** 2026-04-21
**Purpose:** Best-in-class multilingual live transcription for financial meetings
**Languages:** English, Chinese (Mandarin), Japanese, Korean + code-switching

---

## Current State

Tools in `tools/audio_recorder/`:
- Recording: WASAPI loopback (Windows system audio)
- Live transcription: Deepgram nova-2 (English only)
- Offline: faster-whisper (99 languages, local CPU)
- Diarization: Deepgram API (Speaker A/B/C)
- iPhone capture: Screen recording + WiFi upload
- Missing: AI summary, code-switching, CJK live, meeting intelligence

---

## Japanese ASR — Complete Comparison

### Open-Source Models

| Model | CER (clean JA) | JA/EN Code-Switch | Financial Terms | Streaming | Size | Notes |
|-------|----------------|-------------------|-----------------|-----------|------|-------|
| **kotoba-whisper v2.1** | ~5-7% | Poor (biased JA) | Moderate | Chunked | 1.55B | Best single JA model. Fine-tuned Whisper on ReazonSpeech |
| **ReazonSpeech v2 NeMo** | ~5-7% | Poor | Good (broadcast) | Chunked CTC | ~600M | Largest JA training data (35K hrs TV). Best for CTC+LM pipeline |
| **Whisper large-v3** | ~8-12% | Moderate | Mediocre | Chunked | 1.55B | Handles some JA/EN but not specialized |
| **SenseVoice-Small** | ~8-11% | Utterance-level | Unknown | Partial | 234M | Fast, multilingual, emotion detection |
| **ReazonSpeech v2 ESPnet** | ~5-7% | Poor | Good | Batch | ~100M | ESPnet framework dependency |
| **NeMo Conformer JA** | ~6-8% | Poor | Moderate | Transducer streams | ~120M | Solid but less data than ReazonSpeech |
| **wav2vec2 JA** | ~12-18% | None | Poor | No | 315M | Obsolete, superseded by above |

### Commercial APIs

| API | CER (clean JA) | JA/EN Code-Switch | Financial Terms | Hot-Word Boost | Cost/hr |
|-----|----------------|-------------------|-----------------|----------------|---------|
| **AmiVoice** (financial model) | ~3-5% | Good (loanwords) | **Excellent** (purpose-built) | Yes (custom dict) | $$$ (enterprise) |
| **Google Chirp v2** | ~5-7% | Functional | Good + boost | Yes (SpeechAdaptation) | $3.84 |
| **Azure Custom Speech** | ~5-8% | Limited | Good (fine-tunable) | Yes (PhraseList + Custom Speech) | $1.40 |
| **NTT COTOHA** | ~5-7% | Limited | Good | Yes | $$$ (enterprise) |
| **Amazon Transcribe** | ~7-10% | None | Custom Vocabulary | Yes (50K terms) | $1.44 |
| **RECAIUS (Toshiba)** | ~6-8% | Limited | Good | Yes | $$$ (enterprise) |
| **Notta** | ~7-10% | Bilingual mode | Custom vocab (biz plan) | Limited | $14/mo |

### Winner: kotoba-whisper v2.1 (open-source) / AmiVoice (commercial)

---

## Domain Adaptation Methods — Comparison

| Method | Difficulty | Effectiveness | Compute Cost | Risk | Best For |
|--------|-----------|---------------|-------------|------|----------|
| **1. Hot-word boosting** | 1-2/5 | 3/5 | None | Low | Known company/term names |
| **2. LoRA fine-tuning** | 3-4/5 | 4-5/5 | Moderate (GPU hrs) | Medium | When you have domain audio data |
| **3. LM rescoring** | 3/5 | 3-4/5 | Low (N-gram) | Low | CTC models, multi-word phrases |
| **4. Whisper initial_prompt** | 1/5 | 2-3/5 | None | Very low | Quick first improvement |
| **5a. Dictionary post-processing** | 1/5 | 3/5 | None | Very low | Known error patterns |
| **5b. LLM post-correction** | 2/5 | 4/5 | Moderate (API) | Low-medium | Polishing final output |
| **6. Vocabulary augmentation** | 5/5 | 2/5 | High | High | Almost never |

---

## Recommended Architecture

### Language Router + Specialized Models

```
Audio → SenseVoice (detect language, 3s sample, ~100ms)
  │
  ├─ ZH detected → FunASR paraformer-zh + ct-punc
  ├─ EN detected → Whisper large-v3 or paraformer-en
  ├─ JA detected → kotoba-whisper v2.1
  ├─ KO detected → Whisper large-v3 (ko)
  └─ Mixed       → SenseVoice (always-on multilingual)
  
Always: pyannote 3.1 diarization + LLM summary
```

### Three-Layer Architecture (Approach C)

Layer 1: SenseVoice always-on (draft, handles code-switching)
Layer 2: Specialized model in parallel (higher accuracy for dominant language)
Layer 3: Merge — pick higher confidence output per segment

### Per-Scenario Pipeline

| Scenario | Live Preview | Final Transcript | Diarization |
|----------|-------------|-----------------|-------------|
| Pure English | paraformer-en | Whisper large-v3 | pyannote 3.1 |
| Pure Chinese | paraformer-zh-streaming | paraformer-zh | pyannote 3.1 |
| EN/ZH mixed | SenseVoice | SenseVoice + Whisper verify | pyannote 3.1 |
| Pure Japanese | kotoba-whisper | kotoba-whisper v2.1 | pyannote 3.1 |
| JA/EN mixed | kotoba-whisper | kotoba-whisper + dict post-proc | pyannote 3.1 |
| Pure Korean | Whisper (ko) | Whisper large-v3 (ko) | pyannote 3.1 |
| KO/EN mixed | SenseVoice | Whisper (ko) + dict post-proc | pyannote 3.1 |

---

## Domain Adaptation Strategy (Tiered)

### Tier 1 — Quick Win (days)
- Base: kotoba-whisper v2.1 (JA) / paraformer-zh (ZH)
- Whisper initial_prompt with financial terms
- LLM post-correction (Claude/GPT-4o)
- Dictionary-based cleanup for known errors
- Expected: ~6-9% CER general, ~8-12% financial

### Tier 2 — Production Quality (weeks)
- LoRA fine-tune kotoba-whisper on 50-100hrs financial meeting audio
- Mix 70% domain / 30% general data
- Financial term dictionary post-processing
- Expected: ~4-6% CER general, ~5-8% financial

### Tier 3 — Best-in-Class
- Option A: ReazonSpeech v2 NeMo CTC + KenLM financial LM + 200hrs fine-tune → ~3-5% CER
- Option B: AmiVoice financial model (commercial) → ~3-5% CER with minimal engineering

---

## Financial Term Dictionary (seed list)

### Japanese Financial Terms
- 営業利益 (えいぎょうりえき) — operating income
- 一株当たり利益 (ひとかぶあたりりえき) — EPS
- 自己資本利益率 (じこしほんりえきりつ) — ROE
- 設備投資 (せつびとうし) — capex
- 売上高 (うりあげだか) — revenue
- 先端パッケージング (せんたんぱっけーじんぐ) — advanced packaging
- ファウンドリ — foundry

### English Terms in Japanese Context (katakana → English mapping)
- アールオーイー → ROE
- イービットダー → EBITDA
- ピーイー → P/E
- イーピーエス → EPS
- ガイダンス → guidance
- コンセンサス → consensus

### Company Names
- 東京エレクトロン — Tokyo Electron
- ソニーグループ — Sony Group
- トヨタ自動車 — Toyota Motor

---

## GPU Memory Budget (RTX 4090 24GB)

| Model | VRAM | Role |
|-------|------|------|
| SenseVoice-Small | ~1 GB | Always-on: language detect + code-switch |
| fsmn-vad | ~50 MB | Always-on: voice activity detection |
| paraformer-zh | ~1 GB | On-demand: Chinese meetings |
| kotoba-whisper (CTranslate2) | ~4 GB | On-demand: Japanese meetings |
| Whisper large-v3 (CTranslate2) | ~4 GB | On-demand: English/Korean |
| pyannote 3.1 | ~100 MB | Always-on: speaker diarization |
| ct-punc | ~2 GB | Always-on: punctuation |
| **Total always-on** | **~4 GB** | |
| **Total with 1 specialized** | **~8 GB** | |

---

## Implementation Phases

1. Install SenseVoice + test language detection on sample audio
2. Install kotoba-whisper + test on Japanese financial audio
3. Wire as local alternative to Deepgram in live_transcribe.py
4. Add pyannote speaker diarization
5. Add Whisper initial_prompt + dictionary post-processing
6. Add LLM summary layer (Claude API on transcript text)
7. Build Notes tab UI for meeting transcripts
8. Collect financial meeting audio for LoRA fine-tuning (Tier 2)

---

## Model Download Locations

| Model | Source | ID |
|-------|--------|-----|
| kotoba-whisper v2.1 | HuggingFace | kotoba-tech/kotoba-whisper-v2.1 |
| kotoba-whisper faster | HuggingFace | kotoba-tech/kotoba-whisper-v2.1-faster |
| ReazonSpeech v2 NeMo | HuggingFace | reazon-research/reazonspeech-nemo-v2 |
| SenseVoice-Small | HuggingFace | FunAudioLLM/SenseVoiceSmall |
| Whisper large-v3 | HuggingFace | openai/whisper-large-v3 |
| paraformer-zh | ModelScope | iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn |
| pyannote 3.1 | HuggingFace | pyannote/speaker-diarization-3.1 |
| Silero VAD | PyTorch Hub | snakers4/silero-vad |
| ct-punc | ModelScope | iic/punc_ct-transformer_cn-en-common-vocab471067 |
