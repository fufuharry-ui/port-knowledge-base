import subprocess
import threading
import time
from unittest.mock import patch

import pytest
import yaml

from api.compile_jobs import (
    artifact_paths,
    create_artifact_snapshot,
    classify_compile_error,
    restore_artifact_snapshot,
    run_compile_task,
    sanitize_compile_error,
)
from scripts.doc_admin import read_doc_meta, write_doc_compile_result


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("未找到 OPENAI_API_KEY", "llm_configuration"),
        ("401 Unauthorized invalid api key", "llm_configuration"),
        ("Connection refused by provider", "service_unavailable"),
        ("503 Service Unavailable", "service_unavailable"),
        ("500 Internal Server Error", "service_unavailable"),
        ("HTTP status 501", "service_unavailable"),
        ("HTTP status 504", "service_unavailable"),
        ("provider returned 599", "service_unavailable"),
        ("504 Gateway Timeout", "timeout"),
        ("HTTP 400 Bad Request", "compile_failed"),
        ("ReadTimeout request timed out", "timeout"),
        ("找不到原始文本", "document_processing"),
        ("unexpected compiler failure", "compile_failed"),
    ],
)
def test_classify_compile_error(text, expected):
    assert classify_compile_error(text) == expected


def test_sanitize_compile_error_redacts_secrets_and_flattens_lines():
    raw = (
        "Authorization: Bearer token-abc\n"
        "OPENAI_API_KEY=sk-supersecret123\n"
        "https://provider.test/v1?api_key=query-secret&model=x\n"
        "password=hunter2"
    )

    safe = sanitize_compile_error(raw)

    assert "token-abc" not in safe
    assert "sk-supersecret123" not in safe
    assert "query-secret" not in safe
    assert "hunter2" not in safe
    assert "\n" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_limits_to_500_characters():
    safe = sanitize_compile_error("x" * 900)
    assert len(safe) == 500


def test_sanitize_compile_error_redacts_full_authorization_value():
    safe = sanitize_compile_error("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_redacts_env_var_style_secret():
    safe = sanitize_compile_error("OPENAI_API_KEY=plainsecretvalue")
    assert "plainsecretvalue" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_bearer_token_still_fully_redacted():
    safe = sanitize_compile_error("Bearer token-abc")
    assert "token-abc" not in safe
    assert "<redacted>" in safe


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ('{"api_key": "plainsecret-a"}', "plainsecret-a"),
        ("{'x-api-key': 'plainsecret-b'}", "plainsecret-b"),
        ('{"access_token":"plainsecret-c"}', "plainsecret-c"),
        ('{"client_secret": "plainsecret-d"}', "plainsecret-d"),
        ('{"Authorization": "Bearer plainsecret-e"}', "plainsecret-e"),
        ("{'authorization': 'Basic plainsecret-f'}", "plainsecret-f"),
        ('api_key="plainsecret-g"', "plainsecret-g"),
        ("x-api-key='plainsecret-h'", "plainsecret-h"),
    ],
)
def test_sanitize_compile_error_redacts_quoted_credentials(raw, secret):
    """E004-FIX-02:JSON/Python 字典、header 与环境变量形式中带引号的
    凭据键值必须整体脱敏,不能只覆盖无引号键的特例。"""
    safe = sanitize_compile_error(raw)
    assert secret not in safe
    assert "<redacted>" in safe


def test_restore_artifact_snapshot_restores_existing_and_deletes_new(tmp_path):
    base = tmp_path / "repo"
    snapshot_dir = tmp_path / "snapshot"
    doc_id = "doc_20260805_010"
    index = base / "wiki" / "index.yaml"
    summary = base / "wiki" / f"{doc_id}.summary.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"old-index\r\n")

    snapshot = create_artifact_snapshot(base, doc_id, snapshot_dir)

    index.write_bytes(b"new-index\n")
    summary.write_bytes(b"new-summary\n")
    failures = restore_artifact_snapshot(snapshot)

    assert failures == []
    assert index.read_bytes() == b"old-index\r\n"
    assert not summary.exists()


def test_artifact_snapshot_contains_all_seven_paths(tmp_path):
    base = tmp_path / "repo"
    snapshot = create_artifact_snapshot(
        base,
        "doc_20260805_011",
        tmp_path / "snapshot",
    )
    relative = {entry.relative_path for entry in snapshot.entries}
    assert relative == {
        "wiki/doc_20260805_011.summary.yaml",
        "wiki/index.yaml",
        "meta/ontology/doc_20260805_011.ontology.yaml",
        "meta/ontology/global_ontology.yaml",
        "meta/relations/doc_20260805_011.relations.yaml",
        "meta/relations/knowledge_graph.yaml",
        "meta/ontology/entity_relations.yaml",
    }


def test_restore_artifact_snapshot_reports_relative_failure_paths(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_012"
    target = base / "wiki" / "index.yaml"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    snapshot = create_artifact_snapshot(base, doc_id, tmp_path / "snapshot")

    with patch("api.compile_jobs._atomic_write_bytes", side_effect=OSError("denied")):
        failures = restore_artifact_snapshot(snapshot)

    assert failures == ["wiki/index.yaml"]
    assert str(base) not in failures[0]


def _write_meta(base, doc_id, status="compiling"):
    path = base / "raw" / f"{doc_id}.meta.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"id": doc_id, "status": status}),
        encoding="utf-8",
    )


def _write_compile_script(base):
    path = base / "scripts" / "compile.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# test sentinel\n", encoding="utf-8")


def test_run_compile_task_accepts_only_compiled_terminal_state(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_020"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    def fake_run(*_args, **_kwargs):
        write_doc_compile_result(doc_id, "compiled", base_dir=base)
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    assert read_doc_meta(doc_id, base)["status"] == "compiled"


def test_run_compile_task_rolls_back_when_returncode_zero_without_compiled_state(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_021"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    index = base / "wiki" / "index.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"old-index")

    def fake_run(*_args, **_kwargs):
        index.write_bytes(b"partial-index")
        return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert index.read_bytes() == b"old-index"


def test_run_compile_task_records_configuration_failure_before_compile_doc(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_022"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    result = subprocess.CompletedProcess(
        [], 1, stdout="", stderr="RuntimeError: 未找到 OPENAI_API_KEY=sk-secretvalue"
    )
    with patch("api.compile_jobs.subprocess.run", return_value=result):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "llm_configuration"
    assert "sk-secretvalue" not in meta["error_message"]
    assert len(meta["error_message"]) <= 500


def test_run_compile_task_marks_rollback_failed(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_023"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    with patch("api.compile_jobs.subprocess.run", side_effect=OSError("spawn failed")), patch(
        "api.compile_jobs.restore_artifact_snapshot",
        return_value=["wiki/index.yaml"],
    ):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "rollback_failed"
    assert "wiki/index.yaml" in meta["error_message"]


def test_run_compile_task_serializes_different_documents(tmp_path):
    base = tmp_path / "repo"
    docs = ["doc_20260805_024", "doc_20260805_025"]
    for doc_id in docs:
        _write_meta(base, doc_id)
    _write_compile_script(base)
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def fake_run(args, **_kwargs):
        nonlocal active, peak
        doc_id = args[-1]
        with state_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        write_doc_compile_result(doc_id, "compiled", base_dir=base)
        with state_lock:
            active -= 1
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        threads = [threading.Thread(target=run_compile_task, args=(doc_id, base)) for doc_id in docs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

    assert peak == 1
    assert all(read_doc_meta(doc_id, base)["status"] == "compiled" for doc_id in docs)


def test_run_compile_task_missing_script_is_terminal_error(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_026"
    _write_meta(base, doc_id)

    run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert "compile script missing" in meta["error_message"]


def test_run_compile_task_snapshot_failure_is_terminal_error(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_027"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    with patch(
        "api.compile_jobs.create_artifact_snapshot",
        side_effect=OSError("snapshot denied"),
    ), patch("api.compile_jobs.subprocess.run") as mock_run:
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert "snapshot denied" in meta["error_message"]
    mock_run.assert_not_called()


def test_first_compile_failure_removes_all_new_artifacts(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_028"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    targets = artifact_paths(base, doc_id)

    def fake_run(*_args, **_kwargs):
        for index, target in enumerate(targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"new-{index}".encode("utf-8"))
        write_doc_compile_result(
            doc_id,
            "error",
            error_code="compile_failed",
            error_message="child failed",
            base_dir=base,
        )
        return subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr="child failed",
        )

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    assert all(not target.exists() for target in targets)
    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"


def test_recompile_failure_restores_all_artifacts_byte_for_byte(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_029"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    targets = artifact_paths(base, doc_id)
    original = {}
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"old-{index}\r\n".encode("utf-8")
        target.write_bytes(payload)
        original[target] = payload

    def fake_run(*_args, **_kwargs):
        for index, target in enumerate(targets):
            target.write_bytes(f"partial-{index}\n".encode("utf-8"))
        write_doc_compile_result(
            doc_id,
            "error",
            error_code="service_unavailable",
            error_message="provider unavailable",
            base_dir=base,
        )
        return subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr="503 Service Unavailable",
        )

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    for target, payload in original.items():
        assert target.read_bytes() == payload
    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"


def test_run_compile_task_classifies_upstream_500_as_service_unavailable(tmp_path):
    """E004-FIX-03:真实运行器路径——上游 HTTP 5xx 全范围必须归类为
    service_unavailable,且七类产物回滚行为不变。"""
    base = tmp_path / "repo"
    doc_id = "doc_20260806_050"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    targets = artifact_paths(base, doc_id)
    original = {}
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"old-{index}\r\n".encode("utf-8")
        target.write_bytes(payload)
        original[target] = payload

    def fake_run(*_args, **_kwargs):
        for target in targets:
            target.write_bytes(b"partial\n")
        return subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr="ProviderError: HTTP status 500 Internal Server Error",
        )

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    for target, payload in original.items():
        assert target.read_bytes() == payload
    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"
    assert "500 Internal Server Error" in meta["error_message"]
    assert len(meta["error_message"]) <= 500


def test_spawn_oserror_restores_old_artifacts_and_records_error(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_030"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    index = base / "wiki" / "index.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"old-index")

    with patch(
        "api.compile_jobs.subprocess.run",
        side_effect=OSError("connection refused token=secret-value"),
    ):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"
    assert "secret-value" not in meta["error_message"]
    assert index.read_bytes() == b"old-index"


def test_run_compile_task_never_persists_quoted_provider_credentials(tmp_path):
    """E004-FIX-02:真实落盘路径守卫——子进程 stderr 中带引号的凭据字段
    在写入 raw meta 前必须脱敏,不得只测试辅助函数。"""
    base = tmp_path / "repo"
    doc_id = "doc_20260806_040"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    result = subprocess.CompletedProcess(
        [], 1, stdout="", stderr='ProviderError: {"x-api-key": "plainsecret"}',
    )
    with patch("api.compile_jobs.subprocess.run", return_value=result):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta.get("error_message")
    assert "plainsecret" not in meta["error_message"]
    assert "<redacted>" in meta["error_message"]
    assert len(meta["error_message"]) <= 500
    assert "\n" not in meta["error_message"]
