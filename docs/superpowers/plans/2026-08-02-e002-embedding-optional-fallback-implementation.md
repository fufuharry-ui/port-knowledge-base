# E002 Embedding Optional Fallback — Implementation Plan

> Date: 2026-08-02 | Design: `docs/superpowers/specs/2026-08-02-e002-embedding-optional-fallback-design.md`
> Branch: `fix/e002-embedding-optional-fallback` | Base: `origin/dev` @ `15f9d768cc0b9ccbf9957312a106825f2c58dc23`
> 统一执行环境（除注明外）：E002 worktree；清除 `OPENAI_API_KEY`/`EMBEDDING_API_KEY`/`OPENAI_BASE_URL`/`EMBEDDING_BASE_URL`；`PYTHONUTF8=1`；`HTTP_PROXY=http://127.0.0.1:9`；`HTTPS_PROXY=http://127.0.0.1:9`；`NO_PROXY=localhost,127.0.0.1`；解释器 `D:\ProgramData\anaconda3\python.exe`（本机 Anaconda Python 3.12.7）。

## Task 1: 新增离线降级合同测试（RED）

- [ ] **1.1** 创建 `tests/test_embedding_fallback.py`，实现设计 F1–F9 用例：
  - F1 无 Key 降级返回 BM25 命中（顺序按分数）；
  - F2 无 Key + BM25 无命中返回 `[]`；
  - F3 假 Key + `get_embedding` 抛异常仍返回 BM25 结果；
  - F4 假 Key + `Session.post` 抛 `ConnectionError`（真实零向量降级）BM25 无命中返回 `[]`；
  - F5 caplog 验证降级 warning 存在且不含假 Key/`Authorization`；
  - F6 fake client 注入实现 vector-only 召回（>0.5 阈值）；
  - F7 fake client 注入验证 RRF 混合排序增强（d2 超越 d1）；
  - F8 降级模式 top_k=5 恰好返回 5 篇且顺序确定；
  - F9 无 Key 时 `VectorEngine(docs)` 与 `EmbeddingClient()` 仍抛 `RuntimeError`（严格性保持）。
- [ ] **1.2 RED 命令**：`python -m pytest tests/test_embedding_fallback.py -q`
  - 预期：F1–F8 失败（当前 `layer1_filter` 在 Embedding 路径抛 `RuntimeError`/`VectorEngine` 无 `client` 参数）；F9 中严格性断言已绿属预期（锁存现状合同），注入断言失败。
- [ ] **1.3 记录**：每项失败原因截图到任务文档 RED 证据节。

**Commit 边界**：不单独提交；与 Task 2–3 一并进入 Commit 2。

## Task 2: VectorEngine 客户端注入

- [ ] **2.1** 修改 `scripts/search.py`：`VectorEngine.__init__(self, docs, client=None)`；`client is None` 时惰性 `from scripts.embedding_client import EmbeddingClient; client = EmbeddingClient()`；否则使用传入 client。`search` 方法与返回类型不变。
- [ ] **2.2 失败测试**：F6/F7/F9-注入断言（`VectorEngine(docs, client=fake)` 当前 `TypeError`）。
- [ ] **2.3 RED 命令**：`python -m pytest tests/test_embedding_fallback.py::test_vector_engine_accepts_injected_client -q`（F9 注入部分）
- [ ] **2.4 最小实现**：仅构造函数签名与赋值两行变化，不引入全局单例、不引入环境变量。
- [ ] **2.5 GREEN 命令**：`python -m pytest tests/test_embedding_fallback.py -q -k "inject or strict"`
- [ ] **2.6 回归命令**：`python -m pytest tests/test_hybrid_search.py -q`（`test_vector_semantic_search` 此刻仍红，属 Task 4 范围；`test_bm25_exact_match`、`test_rrf_fusion` 必须保持绿）

**Commit 边界**：与 Task 3、4 一并进入 Commit 2。

## Task 3: layer1_filter BM25 降级

- [ ] **3.1** 修改 `scripts/search.py`：
  - 模块顶部 `import logging` + `logger = logging.getLogger(__name__)`；
  - `layer1_filter` 内：docs 空 → `[]`（不变）；本体扩展（不变）；先算 `bm25_scores`（不在 try 内）；`try: vector = VectorEngine(docs); vector_scores = vector.search(query)` / `except Exception as exc: logger.warning(..., type(exc).__name__); vector_scores = {}`；
  - `vector_scores` 为空 → `bm25_scores` 空返回 `[]`，否则按 BM25 原始分数降序截取 `top_k`；
  - `vector_scores` 非空 → 既有 filtered_vector(>0.01)/RRF/阈值 0.5 融合逻辑逐字保留。
- [ ] **3.2 失败测试**：F1–F5、F8。
- [ ] **3.3 RED 命令**（实施前已执行）：`python -m pytest tests/test_embedding_fallback.py -q`
- [ ] **3.4 最小实现**：只新增 try/except + warning + 显式降级分支；不改阈值、RRF、Layer2/3、API。
- [ ] **3.5 GREEN 命令**：`python -m pytest tests/test_embedding_fallback.py -q`（F1–F9 全绿）
- [ ] **3.6 回归命令**：

```powershell
python -m pytest `
  tests/test_search.py::TestLayer1Filter::test_filter_returns_matching_docs `
  tests/test_search.py::TestLayer1Filter::test_filter_respects_top_k `
  tests/test_search.py::TestLayer1Filter::test_filter_no_match `
  tests/test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_thought_sequence `
  tests/test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_entity_extraction `
  -q
```

**Commit 边界**：与 Task 2、4 一并进入 Commit 2。

## Task 4: 修正现有 VectorEngine 测试

- [ ] **4.1** 修改 `tests/test_hybrid_search.py::test_vector_semantic_search`：去除 `@patch("scripts.embedding_client.EmbeddingClient.get_embedding")`；改为 `fake_client = MagicMock(); fake_client.get_embedding.return_value = [0.9, 0.1, 0.0]`；`VectorEngine(docs, client=fake_client)`；断言 `scores["d1"] > scores["d2"]`；断言 `fake_client.get_embedding.assert_called_once_with("网络延迟")`。
- [ ] **4.2 失败测试**：`tests/test_hybrid_search.py::test_vector_semantic_search`（RED 基线已证其失败）。
- [ ] **4.3 RED 命令**：`python -m pytest tests/test_hybrid_search.py::test_vector_semantic_search -q`（基线已记录）
- [ ] **4.4 最小实现**：仅该测试函数体重写；`test_bm25_exact_match`、`test_rrf_fusion` 不动。
- [ ] **4.5 GREEN 命令**：`python -m pytest tests/test_hybrid_search.py -q`
- [ ] **4.6 回归命令**：`python -m pytest tests/test_embedding_fallback.py tests/test_hybrid_search.py -q`

**Commit 边界（Commit 2）**：

```powershell
git add scripts/search.py tests/test_embedding_fallback.py tests/test_hybrid_search.py
git commit -m "fix: make embedding optional in layer1 retrieval"
```

## Task 5: 6 项既有失败转绿

- [ ] **5.1** 运行阶段 I 全部目标命令：
  - `python -m pytest tests/test_embedding_fallback.py -q`
  - 6 项既有失败命令（Task 3.6）
  - 离线检索组：`python -m pytest tests/test_embedding_fallback.py tests/test_search.py tests/test_hybrid_search.py tests/test_api_qa.py -q`
  - `python -m pytest tests/test_startup_smoke.py -q`
  - 99 项核心：`python -m pytest tests/test_ingest.py tests/test_compile.py tests/test_relate.py tests/test_ontology.py tests/test_consistency.py tests/test_doc_admin.py -q`
  - `python -m compileall -q api app scripts tests`
  - `python -m pip check`
- [ ] **5.2** 全部 exit 0 才进入 Task 6；任一失败停止修复后再继续。

**Commit 边界**：无新文件变更（验证任务）。

## Task 6: CI 离线检索门禁

- [ ] **6.1** 修改 `.github/workflows/ci.yml`：`python-core` 在 `Run startup smoke tests` 后插入 `Run offline retrieval tests`（env 与命令见设计文档 CI Gate 节）；不改 job 名、不删步骤、不动其他两个 job。
- [ ] **6.2 验证**：`git diff .github/workflows/ci.yml` 人工核查仅一个新增 step；YAML 语法经 Python `yaml.safe_load` 解析通过。
- [ ] **6.3 回归**：本地按 CI 同等 env 运行离线检索组命令（Task 5.1 已覆盖）。

**Commit 边界（Commit 3 一部分）**。

## Task 7: 文档和治理更新

- [ ] **7.1** 创建 `docs/dev/tasks/E002-embedding-optional-fallback.md`：Status/Base/Branch/Problem/Runtime Contract/Goals/Non-goals/Allowed Files/Implementation/RED 证据/GREEN 证据/离线检索结果/99 项/startup smoke/full pytest 审计/网络隔离/数据 manifest/CI/Codex/Known debt/Follow-up。
- [ ] **7.2** `CLAUDE.md` 最小更新：§5 目标契约标记 E002 已实现并验证（含“EmbeddingClient 直接使用仍保持严格”）；§17 风险 3 标记 E002 已处理；§17 风险 11 更新为 python-core 已增加真正离线检索门禁、完整 pytest 仍保留 1 项独立 LLM mock 债务；不重写其他章节。
- [ ] **7.3** `docs/dev/GIT_WORKFLOW.md`：python-core 事实更新为 install → pip check → compileall → startup smoke → 离线检索测试组 → 99 项确定性核心测试；仍明确不得描述为完整 pytest 绿色。

**Commit 边界（Commit 1 + Commit 3）**：

```powershell
# Commit 1（在 Task 1 开始前先提交设计与计划）
git add docs/superpowers/specs/2026-08-02-e002-embedding-optional-fallback-design.md docs/superpowers/plans/2026-08-02-e002-embedding-optional-fallback-implementation.md
git commit -m "docs: design E002 embedding fallback"

# Commit 3（Task 6+7 完成后）
git add .github/workflows/ci.yml docs/dev/tasks/E002-embedding-optional-fallback.md CLAUDE.md docs/dev/GIT_WORKFLOW.md
git commit -m "ci: enforce offline retrieval fallback"
```

## Task 8: clean venv 与完整 pytest 审计

- [ ] **8.1** 完整 pytest（本机 Anaconda，无 Key 黑洞代理）：`python -m pytest tests/ -q`，输出保存 `$env:TEMP\port-kb-e002-full-pytest.txt`；判定标准：仅可残留 `test_consistency_post_triggers_check`（KNOWN_NON_E002_TEST_ISOLATION_DEBT）。
- [ ] **8.2** 数据隔离复核：测试前后 manifest 对比 + `git status --short -- originals raw wiki meta` + `git ls-files --others --exclude-standard -- originals raw wiki meta`。
- [ ] **8.3** 仓库外创建临时 venv（`$env:TEMP\e002-clean-venv`，Python 3.12）：`pip install -r requirements.txt`（GBK 问题按预案仅在 venv 内升级 pip）→ `pip check` → `compileall` → startup smoke → 离线检索组 → 99 项核心 → 完整 pytest 审计。
- [ ] **8.4** 审计完成后删除 venv；删除失败仅记录路径。

**Commit 边界**：无（验证任务；如 venv 暴露问题需修复则回到对应 Task）。

## Task 9: PR、Codex、合并和清理

- [ ] **9.1** 提交前检查：`git diff --cached --check` / `--name-only` / `--stat`；最终 `git status --short`、`git diff origin/dev..HEAD --check`、`--name-status` 确认范围。
- [ ] **9.2** `git push -u origin fix/e002-embedding-optional-fallback`；创建非 Draft PR（base=dev，标题 `fix: make Embedding optional for offline retrieval`，正文如实含运行时合同/降级证据/混合保持/6 项转绿/CI 门禁/clean venv/full pytest 真实结果/已知债务/无真实 API/无数据污染/未修改 EmbeddingClient/未开始 E003）。
- [ ] **9.3** 等待 repository-integrity / python-core / frontend-unit-build 三绿 → 评论 `@codex review` → 逐条验证处理意见（P0/P1/有效 P2 阻断修复；P3/LOW 技术判断）→ 任何修改新 commit + push + 新 Head CI + 重新 `@codex review`；最多 3 轮。
- [ ] **9.4** 合并前重读 headRefOid/baseRefOid/mergeStateStatus/checks/threads；dev 前进则授权范围内普通 merge origin/dev 进 E002 分支并重走 CI+Codex；满足全部条件后 `gh pr merge --merge --match-head-commit <SHA>`。
- [ ] **9.5** 合并后验证 merge commit 的 dev push CI 三绿；成功后删除远程分支、移除 worktree、`git worktree prune`、`git branch -d` 本地分支；保留 G000 与“知识库研究”、不触碰 E001 残留、不开始 E003。

**Commit 边界**：Codex 修复如需则为独立新 commit（不 amend）。

## 完成定义

最终状态只能是 `E002_COMPLETE_AND_MERGED` 或 `E002_BLOCKED_<具体原因>`；最终报告按任务指令第二十八节输出。
