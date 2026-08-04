from __future__ import annotations

import re
from typing import Literal

CompileErrorCode = Literal[
    "llm_configuration",
    "service_unavailable",
    "timeout",
    "document_processing",
    "compile_failed",
    "rollback_failed",
]

MAX_ERROR_MESSAGE = 500
MAX_CAPTURE_CHARS = 8192

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"), "sk-<redacted>"),
    (
        re.compile(r"(?i)(?<![A-Za-z0-9])(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"(?i)([?&](?:api[_-]?key|apikey|access_token|token|key)=)[^&\s]+"),
        r"\1<redacted>",
    ),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)"),
        r"\1<redacted>",
    ),
)


def sanitize_compile_error(text: str, limit: int = MAX_ERROR_MESSAGE) -> str:
    safe = str(text or "")
    for pattern, replacement in _SECRET_PATTERNS:
        safe = pattern.sub(replacement, safe)
    safe = re.sub(r"\s+", " ", safe).strip()
    return (safe or "编译失败")[:limit]


def classify_compile_error(text: str) -> CompileErrorCode:
    value = str(text or "").lower()
    if any(token in value for token in (
        "openai_api_key", "api key", "api_key", "unauthorized", "invalid key",
        "authentication", "请安装 openai", "no module named 'openai'",
    )):
        return "llm_configuration"
    if any(token in value for token in (
        "timeout", "timed out", "readtimeout", "connecttimeout", "超时",
    )):
        return "timeout"
    if any(token in value for token in (
        "connection refused", "connection error", "service unavailable",
        "bad gateway", "name resolution", "network is unreachable", "服务不可用",
    )) or re.search(r"\b50[23]\b", value):
        return "service_unavailable"
    if any(token in value for token in (
        "找不到原始文本", "document content", "decode", "parse", "内容为空",
    )):
        return "document_processing"
    return "compile_failed"
