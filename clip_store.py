"""Every finished clip, saved as a row in an external Postgres (e.g. Neon).

Self-host keeps clips only as files under output/<job_id>/, deleted after
JOB_RETENTION_SECONDS (24 h), plus the in-memory ``jobs`` dict. That is enough
for the dashboard, not for an automation that picks clips to post over the
next days and records where each one went. With NEON_DATABASE_URL set, the job
worker writes one row per rendered clip here (app.run_job_wrapper, just before
the completion webhook, so a caller notified of the job finds its rows).

The cloud schema (cloud/models.py) is not reused: it needs BILLING_ENABLED, a
``users`` row as foreign key and creates ~20 SaaS tables at boot.

The video itself is not stored. ``file_name`` is what a consumer downloads from
/videos/<job_id>/<file_name> before ``file_expires_at``. ``clip_index`` is the
position in the job's result (what /api/social/post takes), and it shifts if a
restart rebuilds a job one of whose clips failed to render, so prefer the name.

The post_status / scheduled_for / upload_post_id / youtube_url / posted_at /
post_error columns belong to whoever publishes the clip (the n8n workflow): a
re-save of the same job rewrites the content columns only.

Best effort throughout: a missing driver, an unreachable database or a bad URL
logs one line and returns 0; the job has already rendered and stays completed.
"""
import contextlib
import functools
import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

URL_ENV = "NEON_DATABASE_URL"
CONNECT_TIMEOUT = 15  # seconds; a Neon compute waking from scale-to-zero takes ~1 s

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS clips (
        id                    BIGSERIAL PRIMARY KEY,
        job_id                TEXT NOT NULL,
        clip_index            INTEGER NOT NULL,
        file_name             TEXT,
        video_url             TEXT,
        file_expires_at       TIMESTAMPTZ,
        source_url            TEXT,
        title                 TEXT,
        hook_text             TEXT,
        description_tiktok    TEXT,
        description_instagram TEXT,
        start_seconds         DOUBLE PRECISION,
        end_seconds           DOUBLE PRECISION,
        duration_seconds      DOUBLE PRECISION,
        predicted_score       INTEGER,
        why                   TEXT,
        metadata              JSONB,
        post_status           TEXT NOT NULL DEFAULT 'new',
        scheduled_for         TIMESTAMPTZ,
        upload_post_id        TEXT,
        youtube_url           TEXT,
        posted_at             TIMESTAMPTZ,
        post_error            TEXT,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (job_id, clip_index)
    )""",
    "CREATE INDEX IF NOT EXISTS clips_post_queue ON clips (post_status, predicted_score DESC)",
)

CONTENT_COLUMNS = (
    "file_name", "video_url", "file_expires_at", "source_url", "title",
    "hook_text", "description_tiktok", "description_instagram",
    "start_seconds", "end_seconds", "duration_seconds", "predicted_score",
    "why", "metadata",
)

UPSERT = (
    f"INSERT INTO clips (job_id, clip_index, {', '.join(CONTENT_COLUMNS)}) "
    f"VALUES (%(job_id)s, %(clip_index)s, {', '.join(f'%({c})s' for c in CONTENT_COLUMNS)}) "
    "ON CONFLICT (job_id, clip_index) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in CONTENT_COLUMNS)
    + ", updated_at = now()"
)

_dumps = functools.partial(json.dumps, default=str, ensure_ascii=False)
_schema_lock = threading.Lock()
_schema_ready = False


def enabled():
    return bool(os.environ.get(URL_ENV, "").strip())


def _number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def build_rows(job_id, clips, *, source_url=None, base_url="",
               retention_seconds=86400, now=None):
    """One row per clip of a finished job's ``result['clips']``. Pure: the
    caller runs it on the event loop, so the clip dicts are copied before the
    database round trip happens on a thread."""
    now = now or datetime.now(timezone.utc)
    expires = now + timedelta(seconds=retention_seconds)
    base = (base_url or "").rstrip("/")
    rows = []
    for i, clip in enumerate(clips):
        rel = clip.get("video_url") or ""
        start, end = _number(clip.get("start")), _number(clip.get("end"))
        score = _number(clip.get("predicted_score"))
        rows.append({
            "job_id": job_id,
            "clip_index": i,
            "file_name": rel.rsplit("/", 1)[-1] or None,
            "video_url": f"{base}{rel}" if rel.startswith("/") and base else (rel or None),
            "file_expires_at": expires,
            "source_url": source_url,
            "title": clip.get("video_title_for_youtube_short") or clip.get("title"),
            "hook_text": clip.get("viral_hook_text"),
            "description_tiktok": clip.get("video_description_for_tiktok"),
            "description_instagram": clip.get("video_description_for_instagram"),
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": round(end - start, 3) if start is not None and end is not None else None,
            "predicted_score": round(score) if score is not None else None,
            "why": clip.get("why"),
            "metadata": json.loads(_dumps(clip)),
        })
    return rows


def _redacted(error, url):
    """The error text without the password, in case the driver echoes the URL."""
    text = str(error)
    try:
        password = urlsplit(url).password
    except ValueError:
        password = None
    return text.replace(password, "****") if password else text


def write_rows(rows, url=None):
    """Upsert ``rows`` (from build_rows) into ``clips``, creating the table on
    first use. Returns how many rows were written: 0 when the store is off or
    the write failed, which is logged and never raised."""
    global _schema_ready
    url = (url or os.environ.get(URL_ENV, "")).strip()
    if not rows or not url:
        return 0
    try:
        import psycopg
        from psycopg.types.json import Jsonb

        params = [dict(r, metadata=Jsonb(r["metadata"], dumps=_dumps)) for r in rows]
        # Until the table is known to exist, writes go one at a time and the
        # lock is held past the commit: two concurrent CREATE TABLE IF NOT
        # EXISTS can still collide in pg_type with a unique violation.
        with (_schema_lock if not _schema_ready else contextlib.nullcontext()):
            # prepare_threshold=None: with Neon's pooled URL the connection
            # goes through PgBouncer in transaction mode, and psycopg would
            # otherwise switch to server-side prepared statements once a
            # query repeats.
            with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT,
                                 prepare_threshold=None) as conn:
                with conn.cursor() as cur:
                    if not _schema_ready:
                        for statement in SCHEMA:
                            cur.execute(statement)
                    cur.executemany(UPSERT, params)
            _schema_ready = True
        return len(rows)
    except Exception as e:
        print(f"⚠️  Clip store: could not save {len(rows)} clip(s): {_redacted(e, url)}")
        return 0
