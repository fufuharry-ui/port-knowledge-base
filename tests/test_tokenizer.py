"""
tests/test_tokenizer.py — jieba 中文分词工具测试
TDD 先行：测试先于实现 app/utils/tokenizer.py
"""

import sys
import types
import importlib
import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _import_tokenizer():
    """强制重新导入 tokenizer 模块（避免模块缓存干扰隔离测试）"""
    mod_name = "app.utils.tokenizer"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # 确保 app 包可被找到
    import app.utils.tokenizer as tok
    return tok


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 正常中文分词
# ═══════════════════════════════════════════════════════════════════════════════

class TestJiebaTokenize:
    """jieba 中文分词切分逻辑测试"""

    def test_chinese_query_segmented(self):
        """纯中文查询应被切分为含义词组"""
        from app.utils.tokenizer import jieba_tokenize
        tokens = jieba_tokenize("港口岸桥延迟")
        assert isinstance(tokens, list)
        assert len(tokens) >= 2, "中文查询应切出至少 2 个词"
        # 关键词应出现在结果中
        joined = "".join(tokens)
        assert "港口" in joined or "岸桥" in joined or "延迟" in joined

    def test_mixed_chinese_english(self):
        """中英文混合查询应正常切分"""
        from app.utils.tokenizer import jieba_tokenize
        tokens = jieba_tokenize("5G网络MEC延迟要求")
        assert isinstance(tokens, list)
        assert len(tokens) >= 2
        joined = " ".join(tokens)
        # 数字字母应保留
        assert "5G" in joined or "MEC" in joined or "5" in joined

    def test_stopwords_filtered(self):
        """常见停用词（的、了、是、在、和）不应出现在分词结果中"""
        from app.utils.tokenizer import jieba_tokenize
        tokens = jieba_tokenize("港口的自动化是未来的趋势")
        # "的" 是停用词，不应出现
        assert "的" not in tokens

    def test_empty_string_returns_empty_list(self):
        """空字符串分词应返回空列表，不抛异常"""
        from app.utils.tokenizer import jieba_tokenize
        tokens = jieba_tokenize("")
        assert tokens == []

    def test_returns_list_of_strings(self):
        """返回值必须是 list[str]"""
        from app.utils.tokenizer import jieba_tokenize
        tokens = jieba_tokenize("智慧港口远程操控系统")
        assert all(isinstance(t, str) for t in tokens)

    def test_deduplication(self):
        """重复词语在 tokens 中可以出现（不强制去重，保留 BM25 频率语义）"""
        from app.utils.tokenizer import jieba_tokenize
        # 此项仅验证函数不抛异常，不对去重做要求
        tokens = jieba_tokenize("岸桥岸桥岸桥")
        assert isinstance(tokens, list)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. jieba 不可用时的 Graceful Fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestJiebaFallback:
    """当 jieba 未安装时，应退化为空格切词"""

    def test_fallback_on_import_error(self, monkeypatch):
        """模拟 jieba 不存在，应退化为空格切词"""
        # 将 jieba 从 sys.modules 中屏蔽
        monkeypatch.setitem(sys.modules, "jieba", None)

        # 重新导入 tokenizer
        mod_name = "app.utils.tokenizer"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        import importlib
        tok = importlib.import_module("app.utils.tokenizer")

        result = tok.jieba_tokenize("hello world 港口")
        # 退化路径：以空格分词
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_whitespace_tokenize_function_exists(self):
        """tokenizer 模块应导出 whitespace_tokenize 作为兜底"""
        from app.utils.tokenizer import whitespace_tokenize
        assert callable(whitespace_tokenize)
        tokens = whitespace_tokenize("岸桥 远控 延迟")
        assert tokens == ["岸桥", "远控", "延迟"]
