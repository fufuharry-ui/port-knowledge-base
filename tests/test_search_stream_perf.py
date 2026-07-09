"""
test_search_stream_perf.py — Layer3 真流式 + 缓存测试 (Big-Loop #5)
覆盖:
  - layer3_answer_stream: 流式产出 token,末尾附矛盾提示 + 来源
  - 真流式:首 token 不等全生成
"""
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.search import layer3_answer, layer3_answer_stream


def _top_docs():
    return [
        {"id": "doc_A", "title": "岸桥远控方案A"},
        {"id": "doc_B", "title": "岸桥远控方案B"},
    ]


def _index():
    return {"documents": [
        {"id": "doc_A", "title": "岸桥远控方案A"},
        {"id": "doc_B", "title": "岸桥远控方案B"},
    ]}


# ─── P-1: Layer3 真流式 ────────────────────────────────────────────────────

@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream")
def test_layer3_stream_yields_tokens_then_sources(mock_stream, mock_sum, mock_full):
    """P-1: layer3_answer_stream 流式产出 token,末尾附来源。"""
    mock_stream.return_value = iter(["这是", "流式", "答案。"])

    chunks = list(layer3_answer_stream("q", _top_docs(), None, "m", _index()))

    # 答案 token 在前
    assert "这是" in chunks
    assert "流式" in chunks
    assert "答案。" in chunks
    # 来源在后(拼接后包含)
    full = "".join(chunks)
    assert "引用来源" in full
    assert "doc_A" in full


@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream")
def test_layer3_stream_first_token_immediate(mock_stream, mock_sum, mock_full):
    """P-1: 首 token 应立即可得(不等全生成)——generator 惰性。"""
    def slow_gen():
        yield "首token"
        time.sleep(0.05)
        yield "后续"
    mock_stream.return_value = slow_gen()

    gen = layer3_answer_stream("q", _top_docs(), None, "m", _index())
    first = next(gen)
    assert first == "首token"  # 首值立即可得,未等 sleep


@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream")
def test_layer3_stream_includes_contradiction_hint(mock_stream, mock_sum, mock_full):
    """P-1: 流式末尾含矛盾提示(若 Top 文档间有矛盾)。"""
    mock_stream.return_value = iter(["答案。"])
    contradiction = [{
        "doc_a": "doc_A", "doc_b": "doc_B",
        "conflict_point": "延迟", "reasoning_chain": "A说10ms", "confidence": 0.9,
    }]
    chunks = list(layer3_answer_stream(
        "q", _top_docs(), None, "m", _index(), contradictions=contradiction,
    ))
    full = "".join(chunks)
    assert "不一致" in full or "冲突" in full


@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
def test_layer3_stream_no_docs(mock_sum, mock_full):
    """无候选文档 → 流式产出提示并返回(不崩)。"""
    chunks = list(layer3_answer_stream("q", [], None, "m", _index()))
    full = "".join(chunks)
    assert "未找到" in full or "未找到相关文档" in full


@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream")
def test_layer3_answer_nonstream_unchanged(mock_stream, mock_sum, mock_full):
    """回归:非流式 layer3_answer 行为不变(仍返回完整字符串)。"""
    mock_stream.return_value = iter(["完整", "答案。"])
    result = layer3_answer("q", _top_docs(), None, "m", _index())
    assert isinstance(result, str)
    assert "完整答案。" in result
    assert "引用来源" in result


@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream")
def test_layer3_stream_disables_thinking_by_default(mock_stream, mock_sum, mock_full):
    """Big-Loop #5: Layer3 默认关思考(enable_thinking=False)以加速首 token。
    操作侧可设 ANSWER_ENABLE_THINKING=true 开回。"""
    mock_stream.return_value = iter(["答案。"])
    list(layer3_answer_stream("q", _top_docs(), None, "m", _index()))
    _args, kwargs = mock_stream.call_args
    assert kwargs.get("enable_thinking") is False, "Layer3 默认应关思考"


@patch("scripts.search.os.environ", {"ANSWER_ENABLE_THINKING": "true"})
@patch("scripts.search.load_full_text", return_value="全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream")
def test_layer3_stream_enables_thinking_via_env(mock_stream, mock_sum, mock_full):
    """ANSWER_ENABLE_THINKING=true → Layer3 开思考(质量模式)。"""
    mock_stream.return_value = iter(["答案。"])
    list(layer3_answer_stream("q", _top_docs(), None, "m", _index()))
    _args, kwargs = mock_stream.call_args
    assert kwargs.get("enable_thinking") is True
