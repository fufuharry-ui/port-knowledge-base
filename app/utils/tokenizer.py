"""
app/utils/tokenizer.py - jieba Chinese tokenization wrapper
"""

from __future__ import annotations

_STOPWORDS = frozenset({
    "的", "了", "是", "在", "和", "与", "或", "也", "都", "从", "到",
    "把", "被", "将", "用", "等", "及", "对", "中", "对于", "关于",
    "以", "由", "但", "而", "如", "则", "有", "无", "这", "那",
    "为", "于", "使", "让", "我", "你", "他", "她", "我们", "之",
})


def whitespace_tokenize(text: str) -> list[str]:
    return [t for t in text.split() if t.strip()]


def jieba_tokenize(text: str) -> list[str]:
    if not text or not text.strip():
        return []

    try:
        import jieba
        tokens = list(jieba.cut_for_search(text))
    except (ImportError, TypeError):
        return whitespace_tokenize(text)

    result = [
        tok.strip()
        for tok in tokens
        if tok.strip() and tok.strip() not in _STOPWORDS
    ]
    return result