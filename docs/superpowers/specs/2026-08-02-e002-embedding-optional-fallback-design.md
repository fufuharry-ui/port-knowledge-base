# E002 Embedding Optional Fallback Design

> Date: 2026-08-02 | Task: E002 | Branch: `fix/e002-embedding-optional-fallback`
> Base: `origin/dev` @ `15f9d768cc0b9ccbf9957312a106825f2c58dc23`

## Decision

1. **Fallback ownership: `layer1_filter`**（`scripts/search.py`）。混合检索的编排边界负责：先完成 BM25 计算；再尝试初始化并执行 `VectorEngine`；Embedding 不可用时记录不含密钥的 warning；随后用 BM25 结果继续；不向调用方抛出 Embedding 配置或调用异常。
2. **`EmbeddingClient` 保持严格**（`scripts/embedding_client.py` 本任务不修改）：直接实例化且无 `EMBEDDING_API_KEY` 时仍然在构造函数抛出 `RuntimeError`；请求失败时的零向量降级行为保持现状，不在本任务重构；不改造成全局静默 no-op 客户端。可选性只存在于检索编排层。
3. **`VectorEngine` 支持可选客户端注入**：签名扩展为向后兼容的 `VectorEngine(docs, client=None)`。`client is None` 时按现状创建 `EmbeddingClient`；传入 client 时直接使用。不增加全局单例、不增加测试专用环境变量、不把 mock 逻辑写入生产代码。
4. **BM25 是可靠基础检索**：Embedding 不可用时按 BM25 原始分数降序返回并遵守 `top_k`；BM25 无命中返回空列表；不用空向量制造伪命中。
5. **Embedding 只作增强**：Embedding 可用时保持现有混合检索行为——BM25 命中获得向量排序增强（RRF），BM25 无命中但向量余弦超过现有阈值（0.5）仍可向量召回，`reciprocal_rank_fusion` 与 `top_k` 行为不变。
6. **CI 增加真正离线检索测试**：在 `python-core`（job 名称不变）的 startup smoke 之后、99 项核心测试之前插入 `Run offline retrieval tests` 步骤，显式清空密钥、指向黑洞代理运行 4 个检索测试文件。
7. **不处理 LLM consistency mock 债务**：`tests/test_api.py::test_consistency_post_triggers_check` 是 E001 已登记的独立测试隔离债务（mock 了 `run_consistency_check` 但未 mock `get_llm_client`），由后续独立任务处理。

## Current Failure

RED 复现环境（2026-08-02，E002 worktree）：无 `.env`（已核实不存在）、清除 `OPENAI_API_KEY`/`EMBEDDING_API_KEY`/`OPENAI_BASE_URL`/`EMBEDDING_BASE_URL`、`HTTP_PROXY=http://127.0.0.1:9`、`HTTPS_PROXY=http://127.0.0.1:9`、`NO_PROXY=localhost,127.0.0.1`、`PYTHONUTF8=1`、Anaconda Python 3.12.7、pytest 9.0.2。

命令：

```powershell
python -m pytest `
  tests/test_hybrid_search.py::test_vector_semantic_search `
  tests/test_search.py::TestLayer1Filter::test_filter_returns_matching_docs `
  tests/test_search.py::TestLayer1Filter::test_filter_respects_top_k `
  tests/test_search.py::TestLayer1Filter::test_filter_no_match `
  tests/test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_thought_sequence `
  tests/test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_entity_extraction `
  -q
```

结果：**exit code 1，6 failed in 1.00s**。6 项失败与 E001 审计记录完全一致，根因相同：

| 失败测试 | 失败链 |
|---|---|
| `test_hybrid_search.py::test_vector_semantic_search` | `VectorEngine(docs)` → `EmbeddingClient()` → `RuntimeError`（patch 了 `get_embedding` 但仍构造真实客户端） |
| `test_search.py::TestLayer1Filter::test_filter_returns_matching_docs` | `layer1_filter` → `search.py:250` 无条件 `VectorEngine(docs)` → `RuntimeError` |
| `test_search.py::TestLayer1Filter::test_filter_respects_top_k` | 同上 |
| `test_search.py::TestLayer1Filter::test_filter_no_match` | 同上 |
| `test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_thought_sequence` | `app/routers/qa.py:66` → `layer1_filter` → `VectorEngine` → `RuntimeError` |
| `test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_entity_extraction` | 同上 |

全部 6 项均由 Embedding 初始化导致（`scripts/embedding_client.py:30` 的 `RuntimeError: [EmbeddingClient] EMBEDDING_API_KEY 未设置`），无真实网络请求（失败发生在构造函数、尚未发起 HTTP），测试前后 `originals/`、`raw/`、`wiki/`、`meta/` manifest（path→size,mtime_ns）完全一致，`git status` 无数据目录变化。

## Runtime Contract

| 场景 | 合同行为 |
|---|---|
| **no key**（未配置 `EMBEDDING_API_KEY`） | `layer1_filter` 不抛出；记录 warning（含异常类型 `RuntimeError`，不含任何密钥材料）；退化为 BM25-only 检索 |
| **invalid initialization**（配置了 key 但 `VectorEngine`/`EmbeddingClient` 初始化抛异常） | 同上：捕获异常、安全 warning、BM25-only 继续 |
| **embedding request failure**（客户端存在但 `get_embedding` 抛异常） | `VectorEngine.search` 调用异常同样被编排层捕获；BM25-only 继续；不向调用方抛出。（`EmbeddingClient` 自身请求失败返回零向量的现状行为保持不变：零向量余弦为 0，被现有 `>0.01` 过滤，不产生伪命中） |
| **valid embedding** | 保持现有混合检索：BM25 命中文档获得向量 RRF 排序增强；向量分数经 `>0.01` 过滤后参与 `reciprocal_rank_fusion(k=60)` |
| **BM25 hit**（Embedding 不可用） | 按 BM25 原始分数降序返回，分数相同保持文档原顺序（确定性） |
| **BM25 miss**（Embedding 不可用） | 返回 `[]`；不返回所有文档；不用零向量/空向量制造伪命中 |
| **vector-only hit**（Embedding 可用、BM25 无命中） | 向量余弦 `>0.5`（现有阈值）的文档仍可召回；不超过阈值返回 `[]` |
| **empty index** | `documents` 为空立即返回 `[]`，不触碰 Embedding 路径（现有行为，既有测试 `test_filter_empty_index` 覆盖） |

所有场景共同约束：不调用 LLM；不修改文档对象内容；遵守 `top_k`；不引入网络访问（无 key 时 Embedding 路径在构造函数即终止，根本不存在 HTTP 会话）。

## Architecture

```
layer1_filter(query, index, top_k, ontology)
  │
  ├─ docs 为空 → return []
  ├─ 本体查询扩展（现有逻辑，失败降级纯 BM25，不变）
  ├─ BM25Engine(docs).search(expanded_query)        ← 不在任何 try 内；BM25 异常照常上抛
  │
  ├─ try:
  │     VectorEngine(docs)          ← client=None 时构造 EmbeddingClient（严格,可能抛）
  │     vector_scores = .search(query)
  │  except Exception as exc:
  │     logger.warning("Embedding 不可用,降级 BM25 (%s)", type(exc).__name__)
  │     vector_scores = {}
  │
  ├─ vector_scores 为空（不可用/全零被过滤视为不可用的边界由下方既有逻辑处理）
  │     → bm25_scores 为空 → return []
  │     → 否则按 BM25 原始分数降序,截取 top_k 返回
  │
  └─ vector_scores 可用 → 现有融合逻辑不变：
        bm25 非空 → filtered_vector(>0.01) → RRF → 仅返回 BM25 命中文档(向量只做排序增强)
        bm25 为空 → vector_scores(>0.5) → 向量召回
```

`VectorEngine` 注入点只服务于可测试性：`VectorEngine(docs, client=fake)` 让测试用确定性 fake client 验证向量排序与向量召回，不触碰网络、不依赖环境变量。生产调用方（`layer1_filter`、`api/main.py`、`app/routers/qa.py`）签名与返回类型全部不变。

## Error Handling

- **只捕获 Embedding 路径异常**：`try` 范围严格限于 `VectorEngine` 构造 + `vector.search(query)` 两行。BM25 计算、本体扩展（已有自己的降级）、RRF、排序均不在该 `try` 内；BM25 异常照常向调用方抛出。
- **不使用 `except Exception: pass`**：捕获后必须执行 `logger.warning(...)` 并以 `vector_scores = {}` 进入显式降级分支。
- **warning 内容白名单**：固定文案（Embedding 不可用、正在降级为 BM25 检索）+ `type(exc).__name__`（异常类型名）。**不记录**异常 message（可能夹带 URL/请求细节）、API Key、`Authorization` 头、完整请求头或响应体。
- **降级不能伪装成向量检索成功**：降级路径由 `vector_scores == {}` 显式进入，走 BM25 原始分数排序，不经过 RRF 融合，不产生向量召回候选；warning 始终伴随降级出现。
- `EmbeddingClient` 零向量降级（请求失败返回 `[0.0]*1536`）保持现状：零向量余弦为 0.0，被既有 `>0.01` / `>0.5` 阈值过滤，不会制造伪命中。

## Test Design

新增 `tests/test_embedding_fallback.py`（先写测试、确认 RED、再实现）：

| # | 用例 | 要点 |
|---|---|---|
| F1 | 无 Key 自动降级（BM25 有命中） | 删除 `EMBEDDING_API_KEY`；两篇文档 BM25 分数不同；断言返回顺序按 BM25 分数降序、不抛 `RuntimeError`；stub `requests.sessions.Session.send` 若被调用即失败（证明无网络） |
| F2 | 无 Key 且 BM25 无命中 | 查询与文档无关键词重合；断言返回 `[]`、不等于全部文档、无网络 |
| F3 | Embedding 请求失败（客户端调用抛异常） | 设假 Key（明显测试值 `sk-e002-fake-...`）；monkeypatch `EmbeddingClient.get_embedding` 抛 `ConnectionError`；`layer1_filter` 仍返回 BM25 结果、不抛出 |
| F4 | 请求失败零向量不制造伪命中 | 假 Key + stub `Session.post` 抛 `requests.ConnectionError`（走真实 `get_embedding` 零向量降级）；BM25 无命中时返回 `[]`；断言 stub 被调用（HTTP 尝试被拦截,无真实网络） |
| F5 | Warning 安全性 | caplog 捕获降级 warning；断言出现明确 Embedding→BM25 降级记录；断言假 Key 字符串、`Authorization` 均不出现在 `caplog.text`；异常 message 含假 Key 时亦不泄漏（只记类型名） |
| F6 | Embedding 可用：vector-only 召回 | 经 `scripts.search.VectorEngine` 注入确定性 fake client（query/doc 向量固定）；BM25 无命中；d1 余弦 ≈0.99>0.5 召回、d2 余弦 0 不召回；无真实 HTTP |
| F7 | Embedding 可用：混合检索保持 | fake client；3 篇 BM25 命中（rank d1>d2>d3）+ 向量 rank d2>d3>d1；断言 RRF 后 d2 升至首位（纯 BM25 应为 d1），证明向量排序增强生效且行为不变 |
| F8 | 降级遵守 top_k | 30 篇同分 BM25 命中、无 Key、`top_k=5`；断言返回恰好 5 篇且为 `doc_000..doc_004`（同分保持原文档顺序,确定性） |
| F9 | 严格性保持 | 无 Key 时 `VectorEngine(docs)`（不传 client）与直接 `EmbeddingClient()` 仍抛 `RuntimeError`；传入 fake client 则不构造真实客户端 |

修改 `tests/test_hybrid_search.py::test_vector_semantic_search`（阶段 H）：改为 `MagicMock` client 经 `VectorEngine(docs, client=fake)` 注入；`get_embedding` 返回 `[0.9, 0.1, 0.0]`；断言 d1 得分 > d2（不削弱原断言）；断言 `get_embedding` 恰好以 `"网络延迟"` 调用一次；不设真实 Key、不访问网络。

既有 6 项失败测试（`test_search.py` 3 项、`test_api_qa.py` 2 项、`test_hybrid_search.py` 1 项）保持原样（除上述 hybrid 修正外），通过生产修复转绿。

## CI Gate

`.github/workflows/ci.yml` 的 `python-core`（job 名称不变）在 `Run startup smoke tests` 之后、`Run deterministic core tests` 之前插入：

```yaml
      - name: Run offline retrieval tests
        env:
          OPENAI_API_KEY: ""
          EMBEDDING_API_KEY: ""
          OPENAI_BASE_URL: ""
          EMBEDDING_BASE_URL: ""
          HTTP_PROXY: "http://127.0.0.1:9"
          HTTPS_PROXY: "http://127.0.0.1:9"
          NO_PROXY: "localhost,127.0.0.1"
          PYTHONUTF8: "1"
        run: >
          python -m pytest
          tests/test_embedding_fallback.py
          tests/test_search.py
          tests/test_hybrid_search.py
          tests/test_api_qa.py
          -q
```

约束：不更名 `python-core`；不删除现有步骤；不弱化 99 项核心测试；不加入真实密钥；不使用 `continue-on-error` 或 `|| true`；不把完整 pytest 加入 required gate；不修改另外两个 job。空字符串环境变量使 `os.environ.get("EMBEDDING_API_KEY", "")` 返回 falsy，且因 `setdefault` 语义可防御意外 `.env` 覆盖。

## File Scope

允许创建/修改：

- `scripts/search.py`（生产实现，唯一生产文件）
- `tests/test_embedding_fallback.py`（新建）
- `tests/test_hybrid_search.py`（仅 `test_vector_semantic_search` 改注入式）
- `.github/workflows/ci.yml`（仅 `python-core` 插入一个步骤）
- `docs/dev/tasks/E002-embedding-optional-fallback.md`（新建）
- `docs/superpowers/specs/2026-08-02-e002-embedding-optional-fallback-design.md`（本文件）
- `docs/superpowers/plans/2026-08-02-e002-embedding-optional-fallback-implementation.md`（新建）
- `CLAUDE.md`（仅 §5 与 §17 第 3、11 条的最小更新）
- `docs/dev/GIT_WORKFLOW.md`（仅 python-core 事实更新）

明确禁止：`scripts/embedding_client.py`、`tests/test_search.py`、`tests/test_api_qa.py`、`tests/test_api.py`、`tests/conftest.py`、`tests/test_startup_smoke.py`、`api/`、`app/`、`frontend/`、`requirements.txt`、`.env`、`.env.example`、真实数据目录、`ANTIGRAVITY.md`、`.claude/settings.json` 及任何其他文件。

## Non-goals

- 不修改 `EmbeddingClient`：不重构成静默 no-op，不改变其请求失败零向量降级现状；
- 不捕获 BM25 异常、不把所有异常一律降级；
- 不修改 Layer2/Layer3、阈值（0.01/0.5）、RRF 算法、API 接口；
- 不引入向量数据库或任何新依赖；
- 不修复 `test_consistency_post_triggers_check` 的 LLM mock 隔离债务（独立后续任务）；
- 不开始 E003（多轮引用全局状态）；
- 不把完整 pytest 加入 required gate；
- 不统一 `api/` 与 `app/` 两套后端。

## Acceptance Criteria

1. 无 Key、黑洞代理环境下：新增 `tests/test_embedding_fallback.py` 全绿；既有 6 项失败全绿；`test_search.py` + `test_hybrid_search.py` + `test_api_qa.py` + `test_embedding_fallback.py` 四文件离线检索组全绿。
2. `tests/test_startup_smoke.py`、99 项核心测试（6 文件）、`python -m compileall -q api app scripts tests`、`python -m pip check` 全部通过。
3. 完整 `pytest tests/ -q`：无 Embedding 类失败；唯一可接受残留为 `test_consistency_post_triggers_check`（KNOWN_NON_E002_TEST_ISOLATION_DEBT）。
4. 隔离证据：测试前后 `originals/`、`raw/`、`wiki/`、`meta/` manifest（path→size,mtime_ns）一致；`git status`/`git diff` 对数据目录无变化；无真实 Key、无真实网络、无 `.env` 读取（worktree 根目录不存在 `.env`）。
5. 混合检索行为保持：F6/F7 证明 vector-only 召回与 RRF 排序增强不变。
6. CI：`python-core` 含新离线检索步骤且三项 required checks 全绿。
7. 干净 venv（仓库外、Python 3.12）复跑 install/pip check/compileall/smoke/离线组/99 项/完整 pytest，结论与本机一致。
8. PR 经 Codex 审查当前 Head，无未处理的有效 P0/P1/P2；使用 `--match-head-commit` 合并。

## Follow-up

- 独立测试隔离任务：`tests/test_api.py::test_consistency_post_triggers_check` 等用例对 LLM Key 的隐式依赖应显式 mock `get_llm_client`（E001 登记债务,本任务不处理）；
- E003：多轮引用全局状态修复（本任务不开始）；
- 不引入向量数据库——Embedding 仍为可选增强，主架构保持 Context Stuffing 分层检索。
