"""Integration test for POST /api/v1/notes/batch-transcribe-folder.

We patch the transcription function and DB save to keep this test fast
and deterministic. The goal is to exercise the SSE wiring, not the
underlying pipeline (which is covered by the runner unit tests).
"""
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


def _fake_transcribe_ok(path, lang, _glossary, translation):
    return {
        "text": "x",
        "segments": [{"timestamp": "00:01", "speaker": "", "text_original": "Hi"}],
        "language": lang or "en",
        "is_bilingual": False,
        "translation_label": "English",
        "audio_duration_sec": 10.0,
        "input_tokens": 100, "output_tokens": 50,
        "gemini_seconds": 1.0, "total_seconds": 1.5,
        "chunk_count": 1, "chunk_seconds": [10.0],
        "key_topics": [],
    }


def _parse_sse(text: str) -> list[tuple[str, str]]:
    """Cheap SSE parser: returns [(event, data_json), ...]."""
    out, ev, dat = [], None, []
    for line in text.splitlines():
        if not line:
            if ev is not None:
                out.append((ev, "\n".join(dat)))
                ev, dat = None, []
            continue
        if line.startswith("event: "):
            ev = line[len("event: "):]
        elif line.startswith("data: "):
            dat.append(line[len("data: "):])
    return out


def _make_fake_note():
    note = MagicMock()
    note.note_id = "note-fake"
    note.title = "test"
    note.meeting_date = None
    note.company_tickers = []
    note.polished_transcript_meta = {
        "language": "en",
        "is_bilingual": False,
        "segments": [{"timestamp": "00:01", "speaker": "", "text_original": "Hi"}],
        "audio_duration_sec": 10.0,
    }
    return note


def test_batch_endpoint_returns_sse_stream(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"\0" * 1024)

    fake_note = _make_fake_note()
    fake_svc = MagicMock()
    fake_svc.create_note.return_value = fake_note
    fake_svc.get_note.return_value = fake_note

    with patch(
        "backend.app.services.notes.batch_scan.probe_duration_seconds",
        return_value=10.0,
    ), patch(
        "backend.app.api.routers.v1.notes.gemini_batch_transcribe_smart",
        new=_fake_transcribe_ok,
    ), patch(
        "backend.app.api.routers.v1.notes.NotesService",
        return_value=fake_svc,
    ):
        from backend.main import app
        client = TestClient(app)
        resp = client.post("/api/v1/notes/batch-transcribe-folder", json={
            "folder_path":          str(tmp_path),
            "translation_language": "en",
            "note_type":            "meeting_transcript",
            "language":             None,
            "concurrency":          1,
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        kinds = [k for (k, _) in events]
        assert "scan_complete" in kinds, f"events were {kinds}"
        assert "file_start"    in kinds, f"events were {kinds}"
        assert "file_done"     in kinds, f"events were {kinds}"
        assert kinds[-1] == "batch_done", f"events were {kinds}"


def test_batch_endpoint_404_on_missing_folder(tmp_path):
    from backend.main import app
    client = TestClient(app)
    resp = client.post("/api/v1/notes/batch-transcribe-folder", json={
        "folder_path":          str(tmp_path / "does_not_exist"),
        "translation_language": "en",
        "note_type":            "meeting_transcript",
        "language":             None,
        "concurrency":          1,
    })
    assert resp.status_code == 404


def test_batch_endpoint_400_on_path_traversal():
    from backend.main import app
    client = TestClient(app)
    resp = client.post("/api/v1/notes/batch-transcribe-folder", json={
        "folder_path":          "/tmp/../etc",
        "translation_language": "en",
        "note_type":            "meeting_transcript",
        "language":             None,
        "concurrency":          1,
    })
    assert resp.status_code == 400
