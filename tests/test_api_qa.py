"""
tests/test_api_qa.py — Q&A 流式接口 TDD 测试 (Phase D)
覆盖 POST /api/v1/qa (Server-Sent Events 混合流)

事件序列: thought → source → entity → delta → done
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.config import Settings, get_settings


# ─── 共享夹具 ────────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with isolated Settings and mock index"""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "originals").mkdir()
    (tmp_path / "meta" / "ontology").mkdir(parents=True)
    (tmp_path / "meta" / "relations").mkdir(parents=True)

    with open(tmp_path / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "documents": [
                    {
                        "id": "doc_20260405_001",
                        "title": "岸桥远控技术方案",
                        "status": "compiled",
                        "ontology_terms": ["岸桥远控", "5G专网", "MEC"],
                        "abstract_short": "基于5G和MEC的岸桥远程操控系统，延迟≤50ms",
                    },
                    {
                        "id": "doc_20260312_003",
                        "title": "5G港口网络规划",
                        "status": "compiled",
                        "ontology_terms": ["5G专网", "MEC"],
                        "abstract_short": "5G SA独立组网方案",
                    },
                ]
            },
            f,
            allow_unicode=True,
        )

    # 创建对应 raw 文本文件
    (tmp_path / "raw" / "doc_20260405_001.txt").write_text(
        "端到端延迟≤50ms，5G空口延迟≤20ms", encoding="utf-8"
    )
    (tmp_path / "raw" / "doc_20260312_003.txt").write_text(
        "5G SA组网，支持低延迟业务", encoding="utf-8"
    )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://fake.api")
    monkeypatch.setenv("KB_BASE_DIR", str(tmp_path))
    test_settings = Settings()

    from fastapi.testclient import TestClient
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: test_settings
    tc = TestClient(app)
    yield tc, tmp_path
    app.dependency_overrides.clear()


def _parse_sse_events(text: str) -> list[dict]:
    """将 SSE 响应体解析为事件字典列表"""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload and payload != "[DONE]":
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 内容类型与基本结构
# ═══════════════════════════════════════════════════════════════════════════════

class TestQAStreamBasics:
    """POST /api/v1/qa 基本响应结构测试"""

    def test_qa_stream_content_type_is_sse(self, client):
        """响应 Content-Type 必须为 text/event-stream"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator") as mock_gen:
            async def _fake_gen(*args, **kwargs):
                yield 'data: {"type":"done"}\n\n'
            mock_gen.return_value = _fake_gen()

            response = tc.post("/api/v1/qa", json={"query": "岸桥延迟"})

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_qa_missing_query_returns_422(self, client):
        """缺少 query 字段应返回 422"""
        tc, _ = client
        response = tc.post("/api/v1/qa", json={})
        assert response.status_code == 422

    def test_qa_empty_query_returns_422(self, client):
        """空 query 字段应返回 422（min_length=1）"""
        tc, _ = client
        response = tc.post("/api/v1/qa", json={"query": ""})
        assert response.status_code == 422

    def test_qa_response_has_no_cache_headers(self, client):
        """SSE 响应应携带防缓存头"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator") as mock_gen:
            async def _fake_gen(*args, **kwargs):
                yield 'data: {"type":"done"}\n\n'
            mock_gen.return_value = _fake_gen()

            response = tc.post("/api/v1/qa", json={"query": "测试"})

        assert response.headers.get("cache-control") == "no-cache"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 事件序列正确性
# ═══════════════════════════════════════════════════════════════════════════════

class TestQAStreamEventSequence:
    """验证 SSE 事件序列：thought → source → entity → delta → done"""

    def _build_fake_stream(self):
        """构建标准事件序列的假 SSE 流"""
        events = [
            {"type": "thought", "step": 1, "message": "命中本体关键词：[岸桥] [5G专网] [MEC]"},
            {"type": "thought", "step": 2, "message": "Layer2 精选 2 篇高相关文档..."},
            {"type": "thought", "step": 3, "message": "正在加载《岸桥远控技术方案》原文..."},
            {"type": "source", "citations": [
                {"ref": "[1]", "doc_id": "doc_20260405_001",
                 "title": "岸桥远控技术方案", "section": "第3章 网络方案"},
            ]},
            {"type": "entity", "ids": ["doc_20260405_001", "doc_20260312_003"]},
            {"type": "delta", "text": "根据《岸桥远控技术方案》[1]，延迟要求"},
            {"type": "delta", "text": "≤50ms。"},
            {"type": "done"},
        ]

        async def _gen(*args, **kwargs):
            for ev in events:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

        return _gen

    def test_qa_stream_emits_thought_events_before_delta(self, client):
        """thought 事件必须出现在 delta 事件之前"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator", self._build_fake_stream()):
            response = tc.post("/api/v1/qa", json={"query": "岸桥延迟"})

        events = _parse_sse_events(response.text)
        types = [e["type"] for e in events]

        assert "thought" in types
        assert "delta" in types
        # 所有 thought 的最大 index < 所有 delta 的最小 index
        thought_indices = [i for i, t in enumerate(types) if t == "thought"]
        delta_indices = [i for i, t in enumerate(types) if t == "delta"]
        assert max(thought_indices) < min(delta_indices), \
            "所有 thought 事件应出现在 delta 事件之前"

    def test_qa_stream_emits_source_events(self, client):
        """SSE 流中必须包含 source 事件，且 citations 为非空列表"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator", self._build_fake_stream()):
            response = tc.post("/api/v1/qa", json={"query": "岸桥延迟"})

        events = _parse_sse_events(response.text)
        source_events = [e for e in events if e.get("type") == "source"]

        assert len(source_events) >= 1, "至少应有一个 source 事件"
        citations = source_events[0].get("citations", [])
        assert isinstance(citations, list)
        assert len(citations) >= 1
        # 验证 citation 结构
        c = citations[0]
        assert "ref" in c
        assert "doc_id" in c

    def test_qa_stream_emits_entity_events(self, client):
        """SSE 流中必须包含 entity 事件，ids 为非空字符串列表"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator", self._build_fake_stream()):
            response = tc.post("/api/v1/qa", json={"query": "岸桥延迟"})

        events = _parse_sse_events(response.text)
        entity_events = [e for e in events if e.get("type") == "entity"]

        assert len(entity_events) >= 1, "至少应有一个 entity 事件"
        ids = entity_events[0].get("ids", [])
        assert isinstance(ids, list)
        assert len(ids) >= 1
        assert all(isinstance(i, str) for i in ids)

    def test_qa_stream_emits_done_signal(self, client):
        """SSE 流最后一个事件的 type 必须为 done"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator", self._build_fake_stream()):
            response = tc.post("/api/v1/qa", json={"query": "岸桥延迟"})

        events = _parse_sse_events(response.text)
        assert len(events) > 0
        assert events[-1].get("type") == "done", \
            f"最后一个事件应为 done，实际为: {events[-1]}"

    def test_qa_stream_thought_steps_are_ordered(self, client):
        """thought 事件的 step 字段应单调递增（1, 2, 3）"""
        tc, _ = client
        with patch("app.routers.qa.search_stream_generator", self._build_fake_stream()):
            response = tc.post("/api/v1/qa", json={"query": "岸桥延迟"})

        events = _parse_sse_events(response.text)
        thought_steps = [e["step"] for e in events if e.get("type") == "thought"]

        assert thought_steps == sorted(thought_steps), \
            f"thought step 应单调递增，实际: {thought_steps}"
        assert thought_steps[0] == 1, "第一个 thought step 应从 1 开始"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. search_stream_generator 单元测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchStreamGenerator:
    """直接测试 search_stream_generator 异步生成器逻辑"""

    @pytest.fixture()
    def patched_search_mod(self, tmp_path, monkeypatch):
        """设置 search_stream_generator 可用的 search 模块路径"""
        (tmp_path / "raw").mkdir(exist_ok=True)
        (tmp_path / "wiki").mkdir(exist_ok=True)

        with open(tmp_path / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "documents": [
                        {
                            "id": "doc_20260405_001",
                            "title": "岸桥远控技术方案",
                            "status": "compiled",
                            "ontology_terms": ["岸桥"],
                            "abstract_short": "岸桥延迟测试",
                        }
                    ]
                },
                f,
                allow_unicode=True,
            )
        (tmp_path / "raw" / "doc_20260405_001.txt").write_text(
            "延迟≤50ms", encoding="utf-8"
        )

        import scripts.search as smod
        monkeypatch.setattr(smod, "BASE_DIR", tmp_path)
        monkeypatch.setattr(smod, "RAW_DIR", tmp_path / "raw")
        monkeypatch.setattr(smod, "WIKI_DIR", tmp_path / "wiki")
        monkeypatch.setattr(smod, "INDEX_FILE", tmp_path / "wiki" / "index.yaml")
        return smod

    def test_search_stream_generator_thought_sequence(self, patched_search_mod, monkeypatch):
        """生成器应依次 yield 3 条 thought 事件（步骤1/2/3）"""
        import asyncio
        from app.routers.qa import search_stream_generator

        # Mock LLM calls
        monkeypatch.setattr(
            "scripts.search.llm_call_json",
            lambda *a, **k: {"scores": [
                {"doc_id": "doc_20260405_001", "score": 0.9, "reason": "高度相关"}
            ]},
        )
        monkeypatch.setattr(
            "scripts.search.llm_call_text",
            lambda *a, **k: "端到端延迟≤50ms [doc_20260405_001·第3章]",
        )

        mock_client = MagicMock()

        async def collect():
            lines = []
            async for chunk in search_stream_generator("岸桥延迟", mock_client, patched_search_mod):
                if chunk.startswith("data:"):
                    raw = chunk[len("data:"):].strip()
                    try:
                        lines.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
            return lines

        events = asyncio.run(collect())
        thought_events = [e for e in events if e.get("type") == "thought"]

        assert len(thought_events) >= 3, \
            f"应至少有3条 thought 事件，实际: {len(thought_events)}"
        steps = [e["step"] for e in thought_events]
        assert steps == sorted(steps)
        assert 1 in steps and 2 in steps and 3 in steps

    def test_search_stream_generator_entity_extraction(self, patched_search_mod, monkeypatch):
        """entity 事件的 ids 应包含检索命中的 doc_id"""
        import asyncio
        from app.routers.qa import search_stream_generator

        monkeypatch.setattr(
            "scripts.search.llm_call_json",
            lambda *a, **k: {"scores": [
                {"doc_id": "doc_20260405_001", "score": 0.9, "reason": "高度相关"}
            ]},
        )
        monkeypatch.setattr(
            "scripts.search.llm_call_text",
            lambda *a, **k: "延迟≤50ms [doc_20260405_001·第3章]",
        )

        mock_client = MagicMock()

        async def collect():
            lines = []
            async for chunk in search_stream_generator("岸桥延迟", mock_client, patched_search_mod):
                if chunk.startswith("data:"):
                    raw = chunk[len("data:"):].strip()
                    try:
                        lines.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
            return lines

        events = asyncio.run(collect())
        entity_events = [e for e in events if e.get("type") == "entity"]

        assert len(entity_events) >= 1, "应至少有一个 entity 事件"
        ids = entity_events[0].get("ids", [])
        assert "doc_20260405_001" in ids, \
            f"entity ids 应包含命中的 doc_id，实际: {ids}"
