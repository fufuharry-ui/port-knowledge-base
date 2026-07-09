"""
app/utils/tokenizer.py — jieba 中文分词封装
优先使用 jieba 检索模式切词，兼容 jieba 未安装的 graceful fallback。
"""

from __future__ import annotations

# 常见中文停用词表（精简版）
_STOPWORDS = frozenset({
    "的", "了", "是", "在", "和", "与", "或", "也", "都", "从", "到",
    "把", "被", "将", "用", "等", "及", "对", "中", "对于", "关于",
    "以", "由", "但", "而", "如", "则", "有", "无", "这", "那",
    "为", "于", "使", "让", "我", "你", "他", "她", "我们", "之",
})


def whitespace_tokenize(text: str) -> list[str]:
    """按空格切词，作为 jieba 不可用时的兜底。"""
    return [t for t in text.split() if t.strip()]


def jieba_tokenize(text: str) -> list[str]:
    """
    使用 jieba 检索模式（cut_for_search）对文本分词，过滤停用词。
    若 jieba 未安装，自动退化为 whitespace_tokenize。

    Args:
        text: 待分词的查询字符串

    Returns:
        分词列表（已过滤停用词）
    """
    if not text or not text.strip():
        return []

    try:
        import jieba
        # 检索模式：对长词再次切分，提升召回率
        tokens = list(jieba.cut_for_search(text))
    except (ImportError, TypeError):
        # jieba 未安装或被 mock 为 None
        return whitespace_tokenize(text)

    # 过滤停用词和空白 token
    result = [
        tok.strip()
        for tok in tokens
        if tok.strip() and tok.strip() not in _STOPWORDS
    ]
    return result
