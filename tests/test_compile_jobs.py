from unittest.mock import patch

import pytest

from api.compile_jobs import (
    create_artifact_snapshot,
    classify_compile_error,
    restore_artifact_snapshot,
    sanitize_compile_error,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("未找到 OPENAI_API_KEY", "llm_configuration"),
        ("401 Unauthorized invalid api key", "llm_configuration"),
        ("Connection refused by provider", "service_unavailable"),
        ("503 Service Unavailable", "service_unavailable"),
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
