"""
test_consistency_hint.py — Layer3 矛盾提示(Big-Loop #3 C-3/C-4)
覆盖:
  - C-3: Top 文档间存在已知矛盾 → 回答附 "⚠️ 不一致" 提示 + 推理链
  - C-4: 无矛盾 → 回答无提示(不误报)
  - ADR-10: 提示是"附加"非"拦截",Layer3 仍正常回答
"""
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.search import layer3_answer


def _top_docs():
    return [
        {"id": "doc_A", "title": "岸桥远控网络方案A"},
        {"id": "doc_B", "title": "岸桥远控网络方案B"},
    ]


def _index():
    return {"documents": [
        {"id": "doc_A", "title": "岸桥远控网络方案A"},
        {"id": "doc_B", "title": "岸桥远控网络方案B"},
    ]}


_CONTRADICTION = [{
    "doc_a": "doc_A",
    "doc_b": "doc_B",
    "conflict_point": "端到端延迟要求",
    "reasoning_chain": "方案A要求≤10ms,方案B要求≤20ms,数值冲突",
    "confidence": 0.85,
}]


@patch("scripts.search.load_full_text", return_value="文档全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream", return_value=iter(["这是正常生成的答案。"]))
def test_layer3_appends_contradiction_hint(mock_llm, mock_sum, mock_full):
    """C-3: Top 文档间有已知矛盾 → 回答附 ⚠️ 不一致提示 + 推理链。"""
    answer = layer3_answer(
        "岸桥远控网络延迟要求",
        _top_docs(), None, "m", _index(),
        contradictions=_CONTRADICTION,
    )
    # 正常答案仍在(ADR-10:附加非拦截)
    assert "这是正常生成的答案" in answer
    # 矛盾提示存在
    assert "不一致" in answer or "冲突" in answer
    # 推理链的核心冲突点被披露给用户
    assert "延迟" in answer


@patch("scripts.search.load_full_text", return_value="文档全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream", return_value=iter(["这是正常生成的答案。"]))
def test_layer3_no_hint_without_contradiction(mock_llm, mock_sum, mock_full):
    """C-4: 无矛盾 → 回答无 ⚠️ 提示(不误报)。"""
    answer = layer3_answer(
        "岸桥远控网络延迟要求",
        _top_docs(), None, "m", _index(),
        contradictions=[],
    )
    assert "这是正常生成的答案" in answer
    assert "不一致" not in answer
    assert "冲突" not in answer


@patch("scripts.search.load_full_text", return_value="文档全文...")
@patch("scripts.search.load_summary_full", return_value={})
@patch("scripts.search.llm_call_text_stream", return_value=iter(["答案。"]))
def test_layer3_contradiction_outside_top_set_no_hint(mock_llm, mock_sum, mock_full):
    """矛盾对只有一个文档在 Top 集 → 不提示(Top 内部才提示,避免噪声)。"""
    contradiction = [{
        "doc_a": "doc_A",
        "doc_b": "doc_Z",  # doc_Z 不在 Top 集
        "conflict_point": "某冲突",
        "reasoning_chain": "...",
        "confidence": 0.9,
    }]
    answer = layer3_answer(
        "q", _top_docs(), None, "m", _index(), contradictions=contradiction,
    )
    assert "不一致" not in answer
