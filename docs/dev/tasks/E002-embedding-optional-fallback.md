# E002 Embedding Optional Fallback

## Status

- Task: E002
- State: **READY_FOR_PR**（本地验证全部完成；CI/Codex/合并结果以 PR 为准）
- Base: `origin/dev` @ `15f9d768cc0b9ccbf9957312a106825f2c58dc23`（与任务预期 SHA 一致，无漂移）
- Branch: `fix/e002-embedding-optional-fallback`
- Worktree: `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e002`
- Design: `docs/superpowers/specs/2026-08-02-e002-embedding-optional-fallback-design.md`
- Plan: `docs/superpowers/plans/2026-08-02-e002-embedding-optional-fallback-implementation.md`
- Risk: retrieval-core

## Problem

`scripts/search.py` 的 `layer1_filter` 无条件构造 `VectorEngine(docs)`，后者无条件构造 `EmbeddingClient()`；`EmbeddingClient` 在缺少 `EMBEDDING_API_KEY` 时于构造函数抛出 `RuntimeError`（`scripts/embedding_client.py:30`）。结果：Embedding 未配置时，基础关键词检索整体崩溃——违反 CLAUDE.md §5 的目标契约（Embedding 只能作为可选增强）。E001 完整 pytest 审计登记的 6 项 Embedding 缺失类失败均源于此。

## Runtime Contract

| 场景 | 行为 |
|---|---|
| no key | 不抛出；warning（仅含异常类型名）；BM25-only 继续 |
| init failure（有 key 但初始化抛异常） | 同上 |
| request failure（`get_embedding` 抛异常） | 编排层捕获；BM25-only 继续；不向调用方抛出 |
| request failure（真实客户端零向量降级，现状保持） | 零向量余弦 0.0，被既有 >0.01/>0.5 阈值过滤，不产生伪命中 |
| valid embedding + BM25 hit | RRF 混合排序增强（行为不变） |
| valid embedding + BM25 miss（vector-only） | 余弦 >0.5 召回（阈值不变） |
| Embedding 不可用 + BM25 hit | 按 BM25 原始分数降序，同分保持文档原顺序，遵守 top_k |
| Embedding 不可用 + BM25 miss | 返回 `[]`，不返回所有文档 |
| empty index | 立即返回 `[]`（既有行为） |

## Goals

- Embedding 配置有效且调用成功 → 保持关键词与向量混合检索；
- Embedding 未配置、初始化失败或调用失败 → 自动退化为 BM25 关键词检索，基础检索不整体失败；
- 建立真正无密钥、无外网的离线检索 CI 门禁；
- 6 项既有 Embedding 失败通过生产修复转绿（`test_search.py`、`test_api_qa.py` 保持原样）。

## Non-goals

- 不修改 `scripts/embedding_client.py`（直接实例化无 Key 仍抛 `RuntimeError`；零向量降级现状不重构）；
- 不处理 `test_consistency_post_triggers_check` 的 LLM mock 隔离债务（E001 登记，独立后续任务）；
- 不开始 E003（多轮引用）；
- 不引入向量数据库或新依赖；不改 Layer2/3、阈值、RRF、API 接口；
- 不把完整 pytest 加入 required gate；不统一 `api/` 与 `app/`。

## Allowed Files

- `scripts/search.py`、`tests/test_embedding_fallback.py`（新建）、`tests/test_hybrid_search.py`、`.github/workflows/ci.yml`、本文件、设计/计划文档、`CLAUDE.md`（§5、§17 最小更新）、`docs/dev/GIT_WORKFLOW.md`（python-core 事实）。

## Implementation

- `VectorEngine(docs, client=None)`：`client is None` 时按原样构造 `EmbeddingClient`（严格性不变）；传入 client 时直接使用。无全局单例、无测试专用环境变量、生产代码无 mock 逻辑。
- `layer1_filter`：docs 空 → `[]`；本体扩展（不变）；BM25 先算且不在 try 内（BM25 异常照常上抛）；`try: VectorEngine(docs).search(query)` → `except Exception as exc: logger.warning("Embedding 不可用,Layer 1 降级为 BM25 关键词检索 (%s)", type(exc).__name__)` + `vector_scores = {}`；`vector_scores` 为空时按 BM25 原始分数降序截取 top_k（BM25 空则 `[]`）；非空时既有 filtered_vector(>0.01)/RRF/0.5 阈值逻辑逐字保留。
- warning 白名单：固定文案 + 异常类型名；不含 Key、Authorization、请求头、响应体、异常 message。
- `tests/test_embedding_fallback.py`：11 个离线合同用例（F1–F9，见设计文档）。
- `tests/test_hybrid_search.py::test_vector_semantic_search`：改为 MagicMock client 注入；保留 d1>d2 断言并新增 `assert_called_once_with("网络延迟")`。

## RED Evidence

环境：E002 worktree 无 `.env`（已核实）；清除 `OPENAI_API_KEY`/`EMBEDDING_API_KEY`/`OPENAI_BASE_URL`/`EMBEDDING_BASE_URL`；`HTTP_PROXY=http://127.0.0.1:9`、`HTTPS_PROXY=http://127.0.0.1:9`、`NO_PROXY=localhost,127.0.0.1`、`PYTHONUTF8=1`；Anaconda Python 3.12.7、pytest 9.0.2。

- 既有 6 项（实施前）：exit 1，**6 failed in 1.00s**，全部 `RuntimeError: [EmbeddingClient] EMBEDDING_API_KEY 未设置`（`embedding_client.py:30`），无网络请求（构造函数即失败），数据 manifest 前后一致；
- 新增 `tests/test_embedding_fallback.py`（实施前）：exit 1，**8 failed / 3 passed**——F1/F2/F3/F5/F8 失败于同一 `RuntimeError`，F6/F7/F9-inject 失败于 `TypeError: unexpected keyword argument 'client'`；3 项通过为现状锁存（F4 零向量行为、F9 两项严格性）。

## GREEN Evidence

同一离线环境，实施后（全部为本会话新鲜执行）：

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/test_embedding_fallback.py -q` | 0 | **11 passed** in 1.11s |
| 6 项既有失败命令（设计文档 Current Failure 节） | 0 | **6 passed** in 1.28s |

## Offline Retrieval 结果

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/test_embedding_fallback.py tests/test_search.py tests/test_hybrid_search.py tests/test_api_qa.py -q` | 0 | **41 passed** in 23.97s |

## 99 项核心测试

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/test_ingest.py tests/test_compile.py tests/test_relate.py tests/test_ontology.py tests/test_consistency.py tests/test_doc_admin.py -q` | 0 | **99 passed** in 1.29s |

## Startup Smoke

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/test_startup_smoke.py -q` | 0 | **3 passed** in 6.11s |

另：`python -m compileall -q api app scripts tests` exit 0。`pip check` 在 Anaconda base env 因非项目预装包（crewai、opencv-python、streamlit、typer-slim，均不在 `requirements.txt`）相互冲突 exit 1，与 E002 变更无关；权威 `pip check` 以干净 venv 与 CI 为准（同 E001 口径）。

## Full Pytest Audit

命令：`python -m pytest tests/ -q`（同一离线环境；完整输出保存于仓库外 `$env:TEMP\port-kb-e002-full-pytest.txt`）。

**结果：exit 1 — 1 failed, 254 passed in 77.80s。**

分类：

- E002 相关失败：**0**（原 6 项 Embedding 失败全部转绿，新增 11 项全绿）；
- 已知 LLM mock 债务：**1** —— `tests/test_api.py::test_consistency_post_triggers_check`（`assert 'error' == 'success'`；未 mock `get_llm_client`，无 Key 时端点按设计降级返回 `status=error`；与 E001 登记根因一致）→ 标记 `KNOWN_NON_E002_TEST_ISOLATION_DEBT`；
- 其他失败：**0**；无导入失败；无网络访问；无数据污染。

## 网络隔离

- 全程无真实 LLM/Embedding Key；假 Key 仅为明显测试值 `sk-e002-fake-key-not-real-0000`；
- 测试进程设黑洞代理；新增测试以 `no_network` fixture stub `requests.sessions.Session.send`（任何真实 HTTP 发送即失败）；F4 以 stub 拦截 `Session.post` 并断言拦截发生；
- worktree 根目录无 `.env`（已核实），测试未读取用户真实 `.env`；
- 未在日志/输出中打印环境变量值。

## 数据 Manifest

全部测试前与全部测试后分别生成 `originals/`、`raw/`、`wiki/`、`meta/` 清单（path → size, mtime_ns）：**完全一致**。`git status --short -- originals raw wiki meta`、`git diff -- originals raw wiki meta`、`git ls-files --others --exclude-standard -- originals raw wiki meta` 均为空。

## CI

`.github/workflows/ci.yml` 的 `python-core`（job 名不变）在 `Run startup smoke tests` 之后、`Run deterministic core tests` 之前插入 `Run offline retrieval tests`：env 清空四个 Key/URL、黑洞代理、`NO_PROXY`、`PYTHONUTF8=1`，运行 `test_embedding_fallback.py`、`test_search.py`、`test_hybrid_search.py`、`test_api_qa.py` 四文件。未删除/弱化任何现有步骤，未使用 `continue-on-error`/`|| true`，未加入真实密钥，其余两个 job 未改。PR 三项 required checks 结果以 GitHub 运行为准。

## Codex

PR 创建后按流程评论 `@codex review` 触发对当前 Head 的独立审查；意见逐条验证处理（P0/P1 及有效 P2 阻断修复，P3/LOW 技术判断），结论与 thread 状态以 PR 为准。

## Known Debt

- `tests/test_api.py::test_consistency_post_triggers_check`：LLM mock 隔离债务（E001 登记，本任务不修复）——完整 pytest 在无 Key 环境下预期保留此 1 项失败；
- Anaconda base env 的 `pip check` 噪声（非项目包冲突）与本仓库无关，权威验证在干净 venv/CI。

## Follow-up

- 独立测试隔离任务：显式 mock `get_llm_client` 修复 `test_consistency_post_triggers_check`；
- E003：多轮引用全局状态（本任务未开始）；
- Embedding 保持可选增强定位，不引入向量数据库。
