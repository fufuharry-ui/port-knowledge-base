import pytest

from api.compile_jobs import classify_compile_error, sanitize_compile_error


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
