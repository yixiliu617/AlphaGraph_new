---
name: github-prep
description: Prepare the AlphaGraph repo for git commit and GitHub push. Use whenever the user says "push to github", "commit", "clean up for git", or reports a failed push due to large files. Covers .gitignore maintenance, large-file detection, history rewriting when large files leak into commits, and the post-clone setup instructions that must stay accurate in the README. Captures every large-file trap we hit (node_modules, .next, .wav recordings, parquet data, __pycache__) so they don't recur.
---

# GitHub Preparation — Skill

## What this Skill does

Ensures the AlphaGraph repo can be pushed to GitHub without hitting the 100 MB file-size limit, and that the `.gitignore` stays correct as new generated-data directories are added. Also maintains the post-clone setup instructions so collaborators can rebuild derived data from scratch.

## When to use this Skill

- User says "push to github", "commit this", "let's push"
- User reports `remote: error: File ... exceeds GitHub's file size limit of 100.00 MB`
- User adds a new data pipeline that writes large files to disk
- User adds a new frontend dependency or build tool
- Before any PR or branch push

## The .gitignore — what MUST be excluded

These directories contain generated or installed artifacts that are large, reproducible, and must never be committed:

```gitignore
# Node / Next.js
frontend/node_modules/         # 482 MB — recreated by `npm install`
frontend/.next/                # 123 MB — recreated by `npm run dev` or `npm run build`
frontend/.turbo/               # Turborepo cache if used

# Generated financial data (rebuilt by topline/calculator/ingest scripts)
backend/data/filing_data/      # ~200 MB — topline + calculated parquets
backend/data/earnings_releases/ # ~2 MB — 8-K press release parquets
backend/data/earnings_fragments/ # ~254 MB — tagged + embedded fragment parquets
backend/data/insights/          # ~1 MB — margin insights cache + document findings
backend/data/fragment_debug/    # ~5 MB — debug JSON from extraction pipeline

# Keep config (small, hand-edited, not generated)
!backend/data/config/           # ticker_groups.json etc.

# Audio recordings (user-generated, large .wav/.opus files)
tools/audio_recorder/recordings/  # 336 MB — .wav files from meeting recorder

# Python
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/

# Environment
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
NUL
```

### Why each entry exists (the problems we hit)

| Entry | Size when we hit it | What happened |
|---|---|---|
| `frontend/node_modules/` | 482 MB | First push failed — `@next/swc-win32-x64-msvc/next-swc.win32-x64-msvc.node` alone is 177 MB. Standard rule: never commit node_modules. |
| `frontend/.next/` | 123 MB | Next.js build cache. Contains compiled JS, server pages, trace files. Recreated on every `npm run dev`. |
| `tools/audio_recorder/recordings/` | 336 MB | A single `.wav` file (`meeting_recording_20260410_110453.wav`) was 329 MB. Audio recordings are user data, not source code. |
| `backend/data/filing_data/` | ~200 MB | Topline + calculated parquets for 18 tickers. Rebuilt by `ToplineBuilder().build()` + `CalculatedLayerBuilder().build()`. Changes on every rebuild. |
| `backend/data/earnings_fragments/` | 254 MB | Tagged + embedded document fragments. Each ticker's parquet is 8-27 MB due to 3072-dim embedding vectors. Rebuilt by `ingest_fragments.py`. |
| `backend/data/earnings_releases/` | 2 MB | Raw 8-K press release text. Small but generated. Rebuilt by `ingest_earnings_releases.py`. |
| `backend/data/insights/` | ~1 MB | Margin insights cache + document findings parquets. Rebuilt by margin_insights_service and research query service. |

## Pre-push checklist

Run this before every push:

### Step 1 — Check for large files

```bash
git ls-files -z | xargs -0 -I{} sh -c 'sz=$(stat -c%s "{}" 2>/dev/null); [ "$sz" -gt 50000000 ] && echo "$((sz/1048576))MB {}"' | sort -rn
```

If anything appears, it must be added to `.gitignore` and removed from the index:

```bash
git rm -r --cached <path>
```

### Step 2 — Check .gitignore covers new directories

If you added a new data pipeline that writes files to `backend/data/`, or a new tool that generates large artifacts, add the directory to `.gitignore` BEFORE committing.

Common additions that get forgotten:
- New parquet output directories under `backend/data/`
- Model weight files (`.pt`, `.bin`, `.safetensors`)
- Database files (`.db`, `.sqlite`)
- Log files (`.log`)
- Temporary downloads

### Step 3 — Verify the commit is clean

```bash
git status --short | head -20
git diff --cached --stat | tail -5
```

The staged changes should be source code, configs, skills, and documentation only. No binary blobs, no generated data.

## When large files leak into history

If a push fails because large files are in PRIOR commits (not just the current one), `.gitignore` + `git rm --cached` won't help — the old commits still contain them. Options:

### Option A — Orphan branch (best for first push or when history doesn't matter)

This is what we used. Creates a brand-new branch with a single commit containing only the current working tree (which already respects `.gitignore`):

```bash
git checkout --orphan temp_clean
git add -A
git commit -m "Clean initial commit"
git branch -D main
git branch -m main
git push --force origin main
```

**Pros**: simple, guaranteed clean, no tools needed.
**Cons**: loses all commit history.

### Option B — BFG Repo-Cleaner (when history matters)

```bash
# Install BFG (Java required)
java -jar bfg.jar --strip-blobs-bigger-than 50M .git
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push --force
```

**Pros**: preserves commit history, only removes the large blobs.
**Cons**: requires Java + BFG download, still rewrites history (force-push needed).

### Option C — git filter-repo (modern alternative to BFG)

```bash
pip install git-filter-repo
git filter-repo --strip-blobs-bigger-than 50M
git push --force origin main
```

**Pros**: no Java dependency, fast, officially recommended by git.
**Cons**: rewrites history, may need to re-add remote after filtering.

## Post-clone setup instructions

When someone clones the repo, they need to regenerate the derived data. Keep these instructions accurate — if a new data pipeline is added, update this list.

```bash
# 1. Frontend dependencies
cd frontend && npm install && cd ..

# 2. Backend dependencies
cd backend && pip install -r requirements.txt && cd ..

# 3. Environment
cp .env.example .env   # Fill in API keys: ANTHROPIC_API_KEY or GEMINI_API_KEY

# 4. Build topline + calculated layer (fetches from SEC EDGAR, ~15-25 min)
cd backend && python -c "
from app.services.data_agent.topline_builder import ToplineBuilder
from app.services.data_agent.calculator import CalculatedLayerBuilder
ToplineBuilder().build()
CalculatedLayerBuilder().build()
"

# 5. Ingest earnings press releases (~10 min, hits SEC EDGAR)
python scripts/ingest_earnings_releases.py

# 6. Build tagged + embedded fragments (~30 min, uses LLM for tagging + Gemini for embeddings)
python scripts/ingest_fragments.py

# 7. Start backend
uvicorn backend.main:app --reload --port 8000

# 8. Start frontend
cd frontend && npm run dev
```

## Edge cases and gotchas

### The `alphagraph.db` SQLite file

This is the application database (notes, user profiles, etc.). It's currently tracked in git. At ~50 KB it's fine, but if it grows past 10 MB (lots of notes, transcripts), add it to `.gitignore` and store it as generated data.

### The `backend/data/config/` directory

This is the ONE directory under `backend/data/` that IS committed. It contains `ticker_groups.json` (hand-edited sector/supply-chain groupings) and potentially other small config files. The `.gitignore` uses `!backend/data/config/` to explicitly keep it tracked.

### Windows path issues

The repo was developed on Windows. Git may show warnings about CRLF line endings. If collaborators use macOS/Linux, add a `.gitattributes`:

```
* text=auto
*.py text eol=lf
*.ts text eol=lf
*.tsx text eol=lf
*.json text eol=lf
*.md text eol=lf
```

### The `NUL` entry in .gitignore

Windows creates a `NUL` device file that git sometimes tries to track. It's in `.gitignore` as a preventive measure. Don't remove it.

### Parquet files change on every rebuild

Topline and calculated parquets have timestamps and floating-point values that differ slightly across rebuilds (edgartools returns data from SEC's live API, which can change as filings are amended). This means `git diff` on parquet files is meaningless — they're always "changed". This is another reason they belong in `.gitignore`, not in version control.

## Files that implement this skill

| File | Purpose |
|---|---|
| `.gitignore` | The exclusion rules — single source of truth |
| `README.md` | Should contain the post-clone setup instructions (update when adding new data pipelines) |
| This skill file | Documents the reasoning and the problems we hit |
