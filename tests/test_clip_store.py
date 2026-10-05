"""The self-host clip store: what a finished job writes to NEON_DATABASE_URL.

clip_store.build_rows is pure, so the mapping is pinned without a database.
write_rows must never raise (the job already rendered), must leave the columns
the publishing workflow owns alone on a re-save, and app.run_job_wrapper must
write the rows before the webhook tells anyone the job is done.
"""
import asyncio
import inspect
import sys
from datetime import datetime, timedelta, timezone

import pytest

import clip_store

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

CLIP = {
    "start": 21.2,
    "end": 60.07,
    "predicted_score": 87.6,
    "video_title_for_youtube_short": "Stop Making $450 from 3 Million Views",
    "viral_hook_text": "Most people think clipping is just about easy views.",
    "video_description_for_tiktok": "tiktok text",
    "video_description_for_instagram": "instagram text",
    "why": "strong hook",
    "layout_ranges": [{"start": 0.0, "end": 38.87, "layout": "track"}],
    "video_url": "/videos/job-1/subtitled_1_hooked_1_Title_clip_1.mp4",
}

OWNED_BY_PUBLISHER = ("post_status", "scheduled_for", "upload_post_id",
                      "youtube_url", "posted_at", "post_error")


def test_rows_carry_the_clip_and_where_its_file_is():
    [row] = clip_store.build_rows(
        "job-1", [CLIP], source_url="https://www.youtube.com/watch?v=abc",
        base_url="https://api.example.com/", retention_seconds=86400, now=NOW)
    assert row["job_id"] == "job-1" and row["clip_index"] == 0
    assert row["file_name"] == "subtitled_1_hooked_1_Title_clip_1.mp4"
    assert row["video_url"] == ("https://api.example.com/videos/job-1/"
                                "subtitled_1_hooked_1_Title_clip_1.mp4")
    assert row["file_expires_at"] == NOW + timedelta(days=1)
    assert row["source_url"] == "https://www.youtube.com/watch?v=abc"
    assert row["title"] == CLIP["video_title_for_youtube_short"]
    assert row["hook_text"] == CLIP["viral_hook_text"]
    assert row["description_tiktok"] == "tiktok text"
    assert row["description_instagram"] == "instagram text"
    assert row["duration_seconds"] == pytest.approx(38.87)
    assert row["predicted_score"] == 88
    # A copy, so the thread that serialises it never races an edit endpoint.
    assert row["metadata"] == CLIP and row["metadata"] is not CLIP


def test_rows_keep_their_position_and_tolerate_odd_values():
    rows = clip_store.build_rows(
        "j", [{}, {"start": "x", "end": 5, "predicted_score": "nan"}], now=NOW)
    assert [r["clip_index"] for r in rows] == [0, 1]
    for r in rows:
        assert r["file_name"] is None and r["video_url"] is None
        assert r["duration_seconds"] is None and r["predicted_score"] is None


def test_relative_url_is_kept_without_a_base():
    [row] = clip_store.build_rows("job-1", [CLIP], now=NOW)
    assert row["video_url"] == CLIP["video_url"]


def test_a_resave_rewrites_content_but_never_the_publishing_columns():
    update = clip_store.UPSERT.split("DO UPDATE SET", 1)[1]
    for column in OWNED_BY_PUBLISHER:
        assert column in clip_store.SCHEMA[0]
        assert column not in update
    for column in clip_store.CONTENT_COLUMNS:
        assert f"{column} = EXCLUDED.{column}" in update


def test_off_without_a_url(monkeypatch):
    monkeypatch.setenv(clip_store.URL_ENV, "  ")
    assert not clip_store.enabled()
    assert clip_store.write_rows(clip_store.build_rows("j", [CLIP], now=NOW)) == 0


def test_a_missing_driver_is_logged_not_raised(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    rows = clip_store.build_rows("j", [CLIP], now=NOW)
    assert clip_store.write_rows(rows, url="postgresql://u:pw@db.invalid/x") == 0
    assert "Clip store" in capsys.readouterr().out


def test_an_unreachable_database_is_logged_without_the_password(capsys):
    pytest.importorskip("psycopg")
    rows = clip_store.build_rows("j", [CLIP], now=NOW)
    url = "postgresql://user:s3cret-pw@127.0.0.1:1/db"
    assert clip_store.write_rows(rows, url=url) == 0
    out = capsys.readouterr().out
    assert "Clip store" in out and "s3cret-pw" not in out


def test_the_job_worker_saves_before_the_webhook():
    app = pytest.importorskip("app")
    src = inspect.getsource(app.run_job_wrapper)
    assert src.index("_save_clips_to_db(job_id)") < src.index("_notify_job_webhook(job_id)")
    assert "NEON_DATABASE_URL" in app.CHILD_ENV_DENYLIST


def test_only_completed_jobs_with_clips_are_saved(monkeypatch):
    app = pytest.importorskip("app")
    written = []
    monkeypatch.setenv(clip_store.URL_ENV, "postgresql://example/db")
    monkeypatch.setattr(clip_store, "write_rows", lambda rows: written.append(rows) or len(rows))
    jobs = {
        "cs-done": {"status": "completed", "result": {"clips": [CLIP]},
                    "cmd": ["python", "-u", "main.py", "-u", "https://youtu.be/x"],
                    "base_url": "http://testserver/"},
        "cs-failed": {"status": "failed", "result": {"clips": [CLIP]}},
        "cs-empty": {"status": "completed", "result": {"clips": []}},
        "cs-none": {"status": "completed", "result": None},
    }
    monkeypatch.setattr(app, "jobs", {**app.jobs, **jobs})
    for job_id in jobs:
        asyncio.run(app._save_clips_to_db(job_id))
    assert len(written) == 1
    [row] = written[0]
    assert row["job_id"] == "cs-done"
    assert row["source_url"] == "https://youtu.be/x"
    assert row["video_url"] == "http://testserver/videos/job-1/subtitled_1_hooked_1_Title_clip_1.mp4"
