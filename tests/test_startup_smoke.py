"""Startup smoke tests for the dependency and startup baseline (E001).

These tests prove that the two FastAPI entry points and the embedding
client module can be imported, and that both health endpoints answer
``200`` with ``status == "ok"``, in an environment that has:

* no ``.env`` file;
* no LLM / Embedding API keys;
* no usable network (proxy pointed at a black-hole port);
* no real knowledge-base data.

Isolation strategy: the production sources (``api/``, ``app/``,
``scripts/``) are copied into a per-test snapshot under ``tmp_path`` and
imported in a fresh *subprocess* whose ``PYTHONPATH`` points at the
snapshot.  Import-time side effects (``.env`` loading, jieba warm-up,
``ORIGINALS_DIR.mkdir``) therefore land inside the snapshot and can
never touch the real working copy or the user's real ``.env``.

The pytest parent process itself only uses the standard library plus
pytest; it must never import ``api``/``app``/``scripts`` modules.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE_DIRS = ("api", "app", "scripts")
DATA_DIRS = ("originals", "raw", "wiki", "meta")

# Keys/URLs that must NOT leak into the child process.  The whole point
# of the smoke test is proving startup works without them.
SECRET_ENV_VARS = (
    "OPENAI_API_KEY",
    "EMBEDDING_API_KEY",
    "OPENAI_BASE_URL",
    "EMBEDDING_BASE_URL",
)

# Child-process script: three imports + two health checks, results as
# one JSON line on stdout.  Kept inline so no helper module needs to
# exist inside the snapshot.
_CHILD_SCRIPT = r"""
import json
import sys

results = {"imports": [], "responses": {}}

import scripts.embedding_client  # noqa: F401
results["imports"].append("scripts.embedding_client")

import api.main
results["imports"].append("api.main")

import app.main
results["imports"].append("app.main")

from fastapi.testclient import TestClient

api_resp = TestClient(api.main.app).get("/api/v1/health")
results["responses"]["/api/v1/health"] = {
    "status_code": api_resp.status_code,
    "body": api_resp.json(),
}

app_resp = TestClient(app.main.app).get("/health")
results["responses"]["/health"] = {
    "status_code": app_resp.status_code,
    "body": app_resp.json(),
}

print("SMOKE_RESULT " + json.dumps(results, ensure_ascii=False))
"""


def _copy_source_snapshot(tmp_path: Path) -> Path:
    """Copy api/, app/, scripts/ into an isolated snapshot under tmp_path.

    The snapshot deliberately excludes ``.env``, ``.git``, real data
    directories, tests and the frontend.  A minimal directory skeleton
    plus an empty ``wiki/index.yaml`` is created so import-time code
    that expects these paths to exist can run.
    """
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n == "__pycache__" or n.endswith(".pyc")}

    for name in SOURCE_DIRS:
        shutil.copytree(REPO_ROOT / name, snapshot / name, ignore=_ignore)

    for rel in ("raw", "wiki", "meta/ontology", "meta/relations", "originals"):
        (snapshot / rel).mkdir(parents=True, exist_ok=True)

    (snapshot / "wiki" / "index.yaml").write_text(
        "documents: []\n", encoding="utf-8"
    )
    return snapshot


def _child_env(snapshot: Path) -> dict[str, str]:
    """Environment for the child: no secrets, black-hole proxy, UTF-8."""
    env = dict(os.environ)
    for var in SECRET_ENV_VARS:
        env.pop(var, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(snapshot)
    # Black-hole proxy: port 9 (discard) is normally not listening, so
    # any unexpected outbound connection fails fast instead of silently
    # succeeding.  Health checks themselves perform no network I/O.
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    return env


def _run_child(snapshot: Path) -> tuple[subprocess.CompletedProcess, dict]:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        env=_child_env(snapshot),
        cwd=str(snapshot),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    result = None
    for line in proc.stdout.splitlines():
        if line.startswith("SMOKE_RESULT "):
            result = json.loads(line[len("SMOKE_RESULT "):])
    return proc, result


def _data_dir_manifest() -> dict[str, tuple[int, int]]:
    """Read-only manifest (path -> size, mtime_ns) of real data dirs."""
    manifest: dict[str, tuple[int, int]] = {}
    for name in DATA_DIRS:
        root = REPO_ROOT / name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                manifest[str(path.relative_to(REPO_ROOT))] = (
                    stat.st_size,
                    stat.st_mtime_ns,
                )
    return manifest


def _assert_child_ok(proc: subprocess.CompletedProcess, result: dict | None) -> dict:
    assert proc.returncode == 0, (
        f"child exited {proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert result is not None, (
        f"no SMOKE_RESULT line in child output\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    # No API-key-related crash traces should appear on stderr.
    assert "Traceback" not in proc.stderr, proc.stderr
    return result


def test_embedding_client_importable_without_keys(tmp_path: Path) -> None:
    before = _data_dir_manifest()
    proc, result = _run_child(_copy_source_snapshot(tmp_path))
    data = _assert_child_ok(proc, result)
    assert "scripts.embedding_client" in data["imports"]
    assert _data_dir_manifest() == before


def test_api_main_health_ok_without_env(tmp_path: Path) -> None:
    before = _data_dir_manifest()
    proc, result = _run_child(_copy_source_snapshot(tmp_path))
    data = _assert_child_ok(proc, result)
    resp = data["responses"]["/api/v1/health"]
    assert resp["status_code"] == 200
    assert resp["body"]["status"] == "ok"
    assert _data_dir_manifest() == before


def test_app_main_health_ok_without_env(tmp_path: Path) -> None:
    before = _data_dir_manifest()
    proc, result = _run_child(_copy_source_snapshot(tmp_path))
    data = _assert_child_ok(proc, result)
    resp = data["responses"]["/health"]
    assert resp["status_code"] == 200
    assert resp["body"]["status"] == "ok"
    assert _data_dir_manifest() == before
