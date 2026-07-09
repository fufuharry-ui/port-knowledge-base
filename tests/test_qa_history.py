"""
test_qa_history.py — 多轮对话上下文测试 (Loop #8)
验证 Layer3 把对话历史注入 user_prompt,让 LLM 能解析追问代词。
"""
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.search import _build_layer3_context, layer3_answer_stream, layer3_answer


def _top_docs():
    return [{"id": "doc_A", "title": "T"}]


def _index():
    return {"documents": [{"id": "doc_A", "title": "T"}]}


class TestHistoryInjection:
    """history 应被注入 user_prompt,使 LLM 理解追问上下文。"""

    @patch("scripts.search.load_full_text", return_value="全文")
    @patch("scripts.search.load_summary_full", return_value={})
    def test_history_injected_into_prompt(self, mock_sum, mock_full):
        """有 history → user_prompt 含历史问答,LLM 可解析代词。"""
        history = [
            {"role": "user", "content": "岸桥远控用什么网络?"},
            {"role": "assistant", "content": "采用 5G 专网。"},
        ]
        result = _build_layer3_context("那它的延迟要求是多少?", _top_docs(), _index(),
                                       history=history)
        assert result is not None
        user_prompt, _ = result
        # 历史问答出现在 prompt 里
        assert "岸桥远控用什么网络" in user_prompt
        assert "5G 专网" in user_prompt
        # 当前问题也在
        assert "延迟要求" in user_prompt

    @patch("scripts.search.load_full_text", return_value="全文")
    @patch("scripts.search.load_summary_full", return_value={})
    def test_no_history_backward_compatible(self, mock_sum, mock_full):
        """无 history(默认 None)→ 行为不变,向后兼容。"""
        result = _build_layer3_context("查询", _top_docs(), _index())
        assert result is not None
        user_prompt, _ = result
        assert "查询" in user_prompt
        # 无历史区段标记
        assert "对话历史" not in user_prompt

    @patch("scripts.search.load_full_text", return_value="全文")
    @patch("scripts.search.load_summary_full", return_value={})
    def test_empty_history_backward_compatible(self, mock_sum, mock_full):
        """空 history 列表 → 等价于无历史,不注入空区段。"""
        result = _build_layer3_context("查询", _top_docs(), _index(), history=[])
        user_prompt, _ = result
        assert "对话历史" not in user_prompt

    @patch("scripts.search.load_full_text", return_value="全文")
    @patch("scripts.search.load_summary_full", return_value={})
    def test_history_truncated_to_recent(self, mock_sum, mock_full):
        """超长历史(>10 轮)只保留最近若干轮,避免吃掉文档上下文预算。"""
        history = [{"role": "user", "content": f"问题{i}"} for i in range(20)]
        history += [{"role": "assistant", "content": f"答案{i}"} for i in range(20)]
        result = _build_layer3_context("最新问题", _top_docs(), _index(),
                                       history=history)
        user_prompt, _ = result
        # 最早的历史应被截断(问题0 不应出现),最新问题应在
        assert "问题0" not in user_prompt
        assert "最新问题" in user_prompt


class TestLayer3AcceptsHistory:
    """layer3_answer / layer3_answer_stream 接受 history 并透传。"""

    @patch("scripts.search.load_full_text", return_value="全文")
    @patch("scripts.search.load_summary_full", return_value={})
    @patch("scripts.search.llm_call_text_stream", return_value=iter(["答"]))
    def test_stream_accepts_history(self, mock_llm, mock_sum, mock_full):
        """layer3_answer_stream 接受 history 参数(不报错 = 签名正确)。"""
        history = [{"role": "user", "content": "上文问题"}]
        out = "".join(layer3_answer_stream(
            "追问", _top_docs(), None, "m", _index(), history=history,
        ))
        assert "答" in out

    @patch("scripts.search.load_full_text", return_value="全文")
    @patch("scripts.search.load_summary_full", return_value={})
    @patch("scripts.search.llm_call_text_stream", return_value=iter(["答"]))
    def test_nonstream_accepts_history(self, mock_llm, mock_sum, mock_full):
        history = [{"role": "user", "content": "上文问题"}]
        out = layer3_answer(
            "追问", _top_docs(), None, "m", _index(), history=history,
        )
        assert "答" in out
