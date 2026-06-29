"""
sample_data.py — 测试用样本数据和辅助函数
可被各 test 模块直接 import。
"""
import json
from unittest.mock import MagicMock


# ─── Mock LLM ────────────────────────────────────────────────────────────────

def _make_llm_response(content):
    choice = MagicMock()
    if isinstance(content, dict):
        choice.message.content = json.dumps(content, ensure_ascii=False)
    else:
        choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def set_llm_response(client, content):
    client.chat.completions.create.return_value = _make_llm_response(content)


def set_llm_responses(client, contents):
    client.chat.completions.create.side_effect = [_make_llm_response(c) for c in contents]


# ─── 样本文本 ─────────────────────────────────────────────────────────────────

SAMPLE_CHINESE_TEXT = """港口自动化岸桥远控系统技术方案

第一章 系统概述
本方案提出了一种基于5G+MEC的港口岸桥远程操控系统，实现岸桥装卸作业的远程自动化。
系统采用5G SA独立组网方案，端到端延迟控制在50ms以内。

第二章 网络架构
采用5G专网实现低延迟视频回传，MEC边缘计算节点部署在港区。
网络延迟要求：空口延迟≤20ms，端到端延迟≤50ms。
"""

SAMPLE_ENGLISH_TEXT = """Port Automation Research Paper

Abstract
This paper presents a comprehensive analysis of automated port operations
focusing on quay crane remote control and autonomous guided vehicles.
"""

SAMPLE_MIXED_TEXT = """智慧港口 Smart Port Solution

本方案结合 5G network 和 MEC edge computing 技术，
实现 AGV autonomous 导航和 crane remote operation。
"""

# ─── 样本 LLM 响应 ────────────────────────────────────────────────────────────

SAMPLE_SUMMARY_RESPONSE = {
    "abstract": "本方案提出了一种基于5G+MEC的港口岸桥远程操控系统。",
    "key_points": ["采用5G SA独立组网方案", "端到端延迟控制在50ms以内"],
    "sections": [
        {"title": "系统概述", "summary": "介绍整体架构", "page_range": "1-2"},
        {"title": "网络架构", "summary": "5G专网方案", "page_range": "3-5"}
    ],
    "document_type": "technical_spec",
    "writing_style": {
        "tone": "formal-technical",
        "typical_patterns": ["采用...实现..."],
        "key_terminology": {"5G专网": 3, "MEC": 2, "岸桥": 5}
    }
}

SAMPLE_ONTOLOGY_RESPONSE = {
    "ontology_nodes": [
        {"term": "岸桥远控", "parent": "港口自动化", "grandparent": "智慧港口",
         "definition": "远程操控岸桥装卸", "is_new_node": True},
        {"term": "5G专网", "parent": "通信技术", "grandparent": "基础设施",
         "definition": "港口5G SA组网", "is_new_node": True}
    ]
}

SAMPLE_RELATIONS_RESPONSE = {
    "relations": [
        {"target_doc_id": "doc_20260401_001", "type": "supplements",
         "confidence": 0.85, "evidence": "两者均讨论5G在港口的应用"},
        {"target_doc_id": "doc_20260401_002", "type": "same_topic",
         "confidence": 0.72, "evidence": "均涉及岸桥远程操控"}
    ]
}

SAMPLE_SCORE_RESPONSE = {
    "scores": [
        {"doc_id": "doc_20260401_001", "score": 0.92, "reason": "直接相关"},
        {"doc_id": "doc_20260401_002", "score": 0.45, "reason": "间接相关"},
    ]
}
