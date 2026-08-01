# E001 Dependency and Startup Baseline

## Status

- Task: E001
- State: **COMPLETE_AND_MERGED**
- PR: [#3](https://github.com/fufuharry-ui/port-knowledge-base/pull/3) `chore: establish E001 dependency and startup baseline`
- Reviewed Head: `da74075a6037694272ffde6ee5706ec17fe1ef6d`
- Merge commit: `0f50c8e15c23e0ab7e5b8dc09904860d21fc6208`
- Merged at: 2026-08-01T10:55:41Z (base=`dev`)
- Base: `origin/dev` @ `8c183a03ba2172193bb82d6bf07a9256663d14af`
- Feature branch: `chore/e001-dependency-startup-baseline`（合并后已删除）
- Risk: infrastructure
- Production code changes: none
- Design: `docs/superpowers/specs/2026-07-28-e001-dependency-startup-design.md`（方案A）
- Plan: `docs/superpowers/plans/2026-08-01-e001-dependency-startup-implementation.md`

## Problem

- `requirements.txt` 未显式声明多个真实直接依赖（`requests`、`sse-starlette`、`pydantic`、`pytest`），依赖传递依赖偶然安装；
- 无干净环境 install / `pip check` 验证；
- 无"无密钥可启动"冒烟门禁；
- 完整 pytest 在无密钥干净环境下的真实状态未知；
- CI `python-core` 缺少启动冒烟步骤。

## Goal

- 单一 `requirements.txt` 显式覆盖全部真实直接运行依赖与测试依赖；
- 干净环境 `pip install -r requirements.txt` 成功且 `pip check` 通过；
- 新增 `tests/test_startup_smoke.py`：无 `.env`、无密钥、黑洞代理环境下验证 `scripts.embedding_client` / `api.main` / `app.main` 可导入，两套健康检查返回 200 + `status=ok`；
- CI `python-core` 增加 `pip check` 与 startup smoke 两个步骤，job 名称不变；
- 完整 `pytest tests/ -q` 执行并如实审计、按根因分类，不强行变绿，不加入 required gate。

## Non-goals

- 不统一 `api/` 与 `app/` 两套后端；
- 不修改任何生产代码（`api/`、`app/`、`scripts/`）；
- 不修复 Embedding 可选降级（E002）；
- 不修复多轮引用全局状态（E003）；
- 不修改 `tests/conftest.py`、`tests/test_search.py` 或任何现有测试；
- 不引入锁文件或依赖管理平台迁移；
- 不删除 `pytest-asyncio`（声明但当前未使用，见下文观察记录）；
- 不把完整 pytest 加入 required gate；
- 不修改 `.env`、`.env.example`、`frontend/`、真实知识数据。

## Allowed Files

- `requirements.txt`
- `tests/test_startup_smoke.py`（新建）
- `.github/workflows/ci.yml`
- `docs/dev/tasks/E001-dependency-startup-baseline.md`（本文件，新建）
- `docs/superpowers/plans/2026-08-01-e001-dependency-startup-implementation.md`（新建）
- `CLAUDE.md`（仅 §17 第 2、4 条）

## Changes

| 文件 | 变更 |
|---|---|
| `requirements.txt` | 新增 `requests>=2.31.0`、`sse-starlette>=2.1.0`、`pydantic>=2.7.0`、`pytest>=8.2.0`（含注释）；其余行不动 |
| `tests/test_startup_smoke.py` | 新建；源码快照 + 子进程冒烟，3 个用例 |
| `.github/workflows/ci.yml` | `python-core` 插入 `Verify installed dependencies`（pip check）与 `Run startup smoke tests` 两步；其余不动 |
| `CLAUDE.md` | §17 第 2、4 条标记为 E001 已处理并附证据摘要 |

## Validation Evidence

### 干净环境（本地临时 venv，Python 3.12.7，仓库外 `$env:TEMP\e001-clean-venv`，无 `.env`）

实施时 venv 自带 pip 24.2 在 GBK locale 下无法解析 UTF-8 编码的 `requirements.txt`（`UnicodeDecodeError: 'gbk' codec`），按设计预案在 venv 内升级 pip 至 26.2 后继续；此为本地 Windows locale 问题，CI（ubuntu，UTF-8 locale）不受影响。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pip install -r requirements.txt` | 0 | 成功（54 个包，含新增 4 条直接依赖） |
| `pip check` | 0 | No broken requirements found |
| `pytest tests/test_startup_smoke.py -q` | 0 | 3 passed in 8.44s |
| `pytest tests/test_ingest.py tests/test_compile.py tests/test_relate.py tests/test_ontology.py tests/test_consistency.py tests/test_doc_admin.py -q` | 0 | 99 passed in 1.04s |

### 本机开发环境（Anaconda Python 3.12.7）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/test_startup_smoke.py -q` | 0 | 3 passed in 6.03s |

### CI（GitHub Actions，Python 3.11）

PR #3 实际运行结果（workflow run [30695557643](https://github.com/fufuharry-ui/port-knowledge-base/actions/runs/30695557643)，Head `da74075a6037694272ffde6ee5706ec17fe1ef6d`）：

| Check | Status | 时间 | Job |
|---|---|---|---|
| `repository-integrity` | SUCCESS | 2026-08-01T10:21:43Z → 10:21:50Z | [91357729184](https://github.com/fufuharry-ui/port-knowledge-base/actions/runs/30695557643/job/91357729184) |
| `python-core` | SUCCESS | 2026-08-01T10:21:49Z → 10:22:16Z | [91357729188](https://github.com/fufuharry-ui/port-knowledge-base/actions/runs/30695557643/job/91357729188) |
| `frontend-unit-build` | SUCCESS | 2026-08-01T10:21:42Z → 10:22:29Z | [91357729199](https://github.com/fufuharry-ui/port-knowledge-base/actions/runs/30695557643/job/91357729199) |

`python-core` 实际执行了 install、`pip check`、compileall、startup smoke 与 6 文件核心套件，一次通过。

### Codex Review

- 触发方式：PR 评论 `@codex review`；
- 审查 commit：`da74075a60`（即合并前最终 Head）；
- 结论（chatgpt-codex-connector，2026-08-01T10:24:58Z）：**"Didn't find any major issues."**；
- 无 P0–P3 发现；
- 无未解决 review thread。

## Full Pytest Audit（只审计，不要求绿色）

环境：上述干净 venv（Python 3.12.7）；worktree 根目录**无 `.env`**（已核实不存在）；未设置任何 LLM/Embedding Key；未调用真实 API。

命令：`python -m pytest tests/ -q`

**结果：exit code 1 — 7 failed, 237 passed, 1 warning in 33.79s**

### 根因分类

**Embedding 缺失类（6 项 → 登记 E002，不阻断 E001）**

全部失败于 `scripts/embedding_client.py:30` 的 `RuntimeError: [EmbeddingClient] EMBEDDING_API_KEY 未设置`，即 CLAUDE.md §5 已记录的 `layer1_filter` 无条件构造 `VectorEngine` 缺陷：

- `tests/test_hybrid_search.py::test_vector_semantic_search`
- `tests/test_search.py::TestLayer1Filter::test_filter_returns_matching_docs`
- `tests/test_search.py::TestLayer1Filter::test_filter_respects_top_k`
- `tests/test_search.py::TestLayer1Filter::test_filter_no_match`
- `tests/test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_thought_sequence`
- `tests/test_api_qa.py::TestSearchStreamGenerator::test_search_stream_generator_entity_extraction`

**依赖或启动类（0 项）**

无 ImportError / ModuleNotFoundError / collection 错误 / 健康检查失败。E001 范围内的依赖与启动问题为零。

**其他类（1 项 → 登记为后续测试隔离债务，不在 E001 修复）**

- `tests/test_api.py::test_consistency_post_triggers_check`：`assert 'error' == 'success'`。根因：该测试 mock 了 `api.main.run_consistency_check`，但未 mock `get_llm_client`；无 `OPENAI_API_KEY` 时 `scripts/search.py:45` 抛 `RuntimeError`，`POST /api/v1/consistency`（`api/main.py:665-671`）按设计降级返回 `status=error`。即测试隐含依赖 LLM Key 存在，属于既有测试的 mock 完整性问题，与依赖声明、启动基线无关。端点的降级行为本身符合设计（"LLM 不可用 → 返回 error 报告，不崩溃"）。建议由后续测试隔离任务处理。

### 审计边界声明

- 未通过 mock、skip、xfail 强行变绿；
- 未隐藏非零 exit code；
- 完整 pytest **未**加入 required gate；
- 审计未访问真实 `.env`（worktree 中不存在）、未调用真实外部服务、未修改真实知识数据（冒烟测试前后对 `originals/`、`raw/`、`wiki/`、`meta/` 做内容清单只读校验，无变化；`git status` 无数据目录改动）。

## Observations（如实记录，不处理）

- `pytest-asyncio>=0.23.0` 已声明但全仓无 `import pytest_asyncio`、无 `@pytest.mark.asyncio`、无 `asyncio_mode` 配置，当前是未使用的声明；按设计保留，是否移除由用户另行决定。
- 干净环境安装解析出 `pytest 9.1.1`、`fastapi 0.141.1`、`pydantic 2.13.4`、`sse-starlette 3.4.6`、`requests 2.34.2`，与下限约束无冲突，`pip check` 通过。
- starlette 1.3.1 提示 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`——上游演进提示，当前不影响功能，登记观察。

## Follow-up

- E002：Embedding 可选化（修复 6 项 Embedding 缺失类失败的根因 `layer1_filter` 无条件构造 `VectorEngine`），并补真正离线检索测试；
- E003：多轮引用全局状态；
- 后续测试隔离任务：`test_consistency_post_triggers_check` 等对 LLM Key 的隐式依赖应显式 mock；
- `pytest-asyncio` 声明但未使用，是否移除由用户决定。
