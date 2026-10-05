"""The self-host server password: OPENSHORTS_API_TOKEN and app.APITokenGate.

Off by default (self-host has always been open). When set, /api and /mcp
refuse requests without it, before any work starts, while the dashboard's
bootstrap call, CORS preflights, media and health checks stay reachable, and
the 401 stays readable cross-origin so the Vercel dashboard can ask for it.
"""
import asyncio
import json

import httpx
import pytest

app_module = pytest.importorskip("app")

TOKEN = "s3cret-test-token"
ORIGIN = "https://openshorts-test.vercel.app"


def _call(method, path, headers=None, **kw):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.request(method, path, headers=headers or {}, **kw)
    return asyncio.run(_do())


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setattr(app_module, "API_TOKEN", TOKEN)
    return TOKEN


def test_off_by_default(monkeypatch):
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    assert _call("GET", "/api/status/no-such-job").status_code == 404
    assert _call("GET", "/api/config").json()["serverPassword"] is False


def test_api_refuses_without_the_password(password):
    refused = _call("GET", "/api/status/no-such-job")
    assert refused.status_code == 401
    assert refused.headers["x-openshorts-auth"] == "required"
    assert _call("GET", "/api/status/no-such-job",
                 headers={"X-OpenShorts-Token": "wrong"}).status_code == 401
    assert _call("GET", "/api/status/no-such-job",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_header_or_bearer_lets_it_through(password):
    assert _call("GET", "/api/status/no-such-job",
                 headers={"X-OpenShorts-Token": password}).status_code == 404
    assert _call("GET", "/api/status/no-such-job",
                 headers={"Authorization": f"Bearer {password}"}).status_code == 404


def test_a_job_is_refused_before_any_work(password, monkeypatch):
    probed = []
    monkeypatch.setattr(app_module, "_probe_youtube_quality", lambda *a, **k: probed.append(a))
    r = _call("POST", "/api/process",
              json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "acknowledged": True})
    assert r.status_code == 401
    assert not probed


def test_bootstrap_media_and_health_stay_open(password):
    cfg = _call("GET", "/api/config")
    assert cfg.status_code == 200 and cfg.json()["serverPassword"] is True
    assert _call("GET", "/health").status_code == 200
    # <video src> cannot send headers; the job id is the capability, like /videos.
    assert _call("GET", "/api/source/no-such-job").status_code != 401
    assert _call("POST", "/api/source/no-such-job").status_code == 401
    assert _call("GET", "/api/source-url/no-such-job").status_code == 401
    assert _call("GET", "/videos/no-such-job/clip.mp4").status_code != 401


def test_preflight_passes_and_the_refusal_is_readable_cross_origin(password):
    pre = _call("OPTIONS", "/api/process", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-openshorts-token"})
    assert pre.status_code == 200
    refused = _call("GET", "/api/status/x", headers={"Origin": ORIGIN})
    assert refused.status_code == 401
    assert refused.headers.get("access-control-allow-origin")
    assert "X-OpenShorts-Auth" in refused.headers.get("access-control-expose-headers", "")


def test_mcp_needs_it_and_carries_it_into_the_tool_call(password):
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert _call("POST", "/mcp", json=listing).status_code == 401
    assert _call("POST", "/mcp", json=listing,
                 headers={"Authorization": f"Bearer {password}"}).status_code == 200

    app_module.jobs["token-test-job"] = {"status": "completed", "logs": ["done"], "result": {"clips": []}}
    try:
        # The tool calls back into /api/status in-process, through the same gate.
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "get_job_status", "arguments": {"job_id": "token-test-job"}}}
        resp = _call("POST", "/mcp", json=call, headers={"X-OpenShorts-Token": password})
    finally:
        app_module.jobs.pop("token-test-job", None)
    assert resp.status_code == 200
    text = json.dumps(resp.json())
    assert "completed" in text and "needs its password" not in text


def test_the_password_never_reaches_the_pipeline(monkeypatch):
    assert "OPENSHORTS_API_TOKEN" in app_module.CHILD_ENV_DENYLIST
    monkeypatch.setenv("OPENSHORTS_API_TOKEN", TOKEN)
    # Blank, not absent: main.py's load_dotenv() would refill an absent one.
    assert app_module.child_env()["OPENSHORTS_API_TOKEN"] == ""


def test_a_root_path_does_not_open_the_gate(password):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app, root_path="/os")
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return [await c.get("/os/api/status/x"),
                    await c.post("/os/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})]
    for resp in asyncio.run(_do()):
        assert resp.status_code == 401


def test_api_docs_go_behind_the_password(password):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert _call("GET", path).status_code == 401
        assert _call("GET", path, headers={"X-OpenShorts-Token": password}).status_code == 200


def test_upload_slot_is_its_own_capability_but_minting_one_is_not(password):
    # An agent PUTs the bytes with a bare curl -T to the URL create_upload returned.
    assert _call("PUT", "/api/uploads/no-such-slot", content=b"x").status_code != 401
    assert _call("DELETE", "/api/uploads/no-such-slot").status_code != 401
    assert _call("POST", "/api/uploads", json={"filename": "a.mp4"}).status_code == 401
    assert _call("PUT", "/api/uploads/a/b", content=b"x").status_code == 401


def test_config_does_not_leak_the_local_llm_address(password, monkeypatch):
    monkeypatch.setattr(app_module.llm_backend, "describe",
                        lambda: {"provider": "openai-compatible", "model": "m", "baseUrl": "http://10.0.0.5:8080/v1"})
    llm = _call("GET", "/api/config").json()["localLlm"]
    assert llm == {"provider": "openai-compatible", "model": "m"}


def test_stdio_transport_sends_the_password():
    # A real subprocess, as tests/test_mcp_stdio.py does: mcp_stdio swaps
    # sys.stdout at import. Its tool call goes back through the gate in-process.
    import os
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, BILLING_ENABLED="0", PYTHONPATH=repo, OPENSHORTS_API_TOKEN=TOKEN)
    env.pop("PROXY_URL", None)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "get_job_status", "arguments": {"job_id": "no-such-job"}}},
    ]
    proc = subprocess.run(
        [sys.executable, "-u", os.path.join(repo, "mcp_stdio.py")],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, cwd=repo, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    replies = {r["id"]: r for r in map(json.loads, filter(str.strip, proc.stdout.splitlines()))}
    text = json.dumps(replies[2])
    assert "needs its password" not in text and "not found" in text.lower()
