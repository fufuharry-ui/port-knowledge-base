# T001 Full Offline Pytest Closure

## Status

- Task: T001
- State: **IMPLEMENTED_AND_VALIDATED**（本地与干净 venv 验证全部完成；GitHub PR、Codex 与 merge 状态以 PR 及最终任务报告为权威记录）
- Base: `origin/dev` @ `f171aebdd975d10c70f3de76a38d71272a342027`（与任务预期 SHA 一致，无漂移）
- Branch: `chore/t001-full-pytest-closure`
- Worktree: `D:\administrator\Desktop\大模型产品化\port-knowledge-base-t001`
- Design: `docs/superpowers/specs/2026-08-02-t001-full-pytest-closure-design.md`
- Plan: `docs/superpowers/plans/2026-08-02-t001-full-pytest-closure-implementation.md`
- Risk: test-infrastructure
- Production code changes: none

## Problem

E002 合并后，无 Key、无 `.env`、黑洞代理环境下完整 `pytest tests/ -q` 为 254 passed / 1 failed，唯一失败为 `tests/test_api.py::test_consistency_post_triggers_check`（E001 登记的 LLM mock 隔离债务）。同时 `tests/test_api.py::test_consistency_post_degrades_on_error` 只 mock 了 `run_consistency_check`，无 Key 时其 `status=error` 实际来自 `get_llm_client` 提前失败，构成对其声明意图的假阳性。CI `python-core` 尚无完整 `tests/` 门禁。

## Root Cause

`POST /api/v1/consistency`（`api/main.py:659-678`）的调用顺序为 `get_llm_client()` → 确定 model → `run_consistency_check(client, model)`。`get_llm_client` 在缺少 `OPENAI_API_KEY` 时抛 `RuntimeError`（`scripts/search.py:48`）。两个一致性 POST 测试均未 mock `get_llm_client`，因此：

- success 测试：mock 的 `run_consistency_check` 从未执行，端点按既有设计降级返回 `status=error`，断言 `assert 'error' == 'success'` 失败——测试隔离问题，非生产缺陷；
- error 测试：`status=error` 碰巧成立，但错误来源是无 Key 的客户端创建失败，而非被 mock 抛异常的一致性检查过程——假阳性。

## Non-goals

- 不修改任何生产代码（`api/`、`app/`、`scripts/` 零变更）；端点降级契约（HTTP 200 + `status` 区分 success/error）保持不变；
- 不统一双后端；不处理其他已登记工程风险；不开始 E003；
- 不新增依赖、不改 `requirements.txt`；不把完整 pytest 绿色解释为前端 E2E 或真实栈 UAT 已完成。

## Allowed Files

- `tests/test_api.py`（仅两个一致性 POST 测试）
- `.github/workflows/ci.yml`（仅 `python-core` 新增最终步骤）
- `docs/dev/tasks/T001-full-pytest-closure.md`（本文件）
- `docs/superpowers/specs/2026-08-02-t001-full-pytest-closure-design.md`
- `docs/superpowers/plans/2026-08-02-t001-full-pytest-closure-implementation.md`
- `CLAUDE.md`（仅 §17 第 4、11 条与版本头）
- `docs/dev/GIT_WORKFLOW.md`（仅 python-core 事实）

## RED

环境：T001 worktree（根目录无 `.env`，已核实）；命令行内联 `env -u` 清除 `OPENAI_API_KEY`/`EMBEDDING_API_KEY`/`OPENAI_BASE_URL`/`EMBEDDING_BASE_URL`（父进程环境不修改、值不打印）；黑洞代理 `HTTP(S)_PROXY=http://127.0.0.1:9` + `NO_PROXY=localhost,127.0.0.1`；`PYTHONUTF8=1`；Anaconda Python 3.12.7、pytest 9.1.1。

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/test_api.py::test_consistency_post_triggers_check -q` | 1 | `AssertionError: assert 'error' == 'success'`（test_api.py:339），1 failed |
| `pytest tests/ -q` | 1 | **1 failed, 254 passed, 7 warnings in 73.44s**；唯一失败即目标测试，无未知失败 |

数据 manifest（`originals/`、`raw/`、`wiki/`、`meta/`，path|size|mtime_ns，76 项）RED 前后完全一致；Git 数据目录无变化。

## Implementation

- `test_consistency_post_triggers_check`：新增 `@patch("api.main.get_llm_client")` 返回 `MagicMock(name="consistency_llm_client")` fake client；`monkeypatch.setenv("RELATE_MODEL", "test-relate-model")` 固定模型；保留既有 success/total/conflict_point 断言；新增 `mock_get_client.assert_called_once_with()` 与 `mock_run.assert_called_once_with(fake_client, "test-relate-model")`。
- `test_consistency_post_degrades_on_error`：同样显式 mock `get_llm_client` 成功并固定 `RELATE_MODEL`；`run_consistency_check` 保持 `side_effect=Exception("LLM down")`；保留 status=error/total=0 断言；新增同样两条调用断言，证明 error 确定性来自一致性检查过程。
- 复用文件已有 `MagicMock` 导入，无新增依赖；未使用 skip/xfail；未放宽断言。

## Target Tests（GREEN，同一离线环境）

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/test_api.py::test_consistency_post_triggers_check tests/test_api.py::test_consistency_post_degrades_on_error -q` | 0 | **2 passed** in 1.16s |
| `pytest tests/test_api.py -q` | 0 | **25 passed** in 2.00s |

## Full Pytest（GREEN，同一离线环境）

| 命令 | Exit | 结果 |
|---|---|---|
| `pytest tests/ -q` | 0 | **255 passed, 7 warnings in 72.36s**；failed=0，error=0；warning 未隐藏（Anaconda 全局环境 `pkg_resources` 弃用提示，与仓库无关） |

## Clean Venv

仓库外临时 venv（`$TEMP\port-kb-t001-clean-venv`，Python 3.12.7，验证完成后已删除）。系统 pip 24.2 在 `PYTHONUTF8=1` 下成功解析 UTF-8 `requirements.txt`，无需升级 pip；未修改系统 Python。

| 命令 | Exit | 结果 |
|---|---|---|
| `pip install -r requirements.txt` | 0 | 成功 |
| `pip check` | 0 | No broken requirements found |
| `compileall -q api app scripts tests` | 0 | 通过 |
| `pytest tests/test_startup_smoke.py -q` | 0 | **3 passed** in 5.50s |
| `pytest tests/test_embedding_fallback.py tests/test_search.py tests/test_hybrid_search.py tests/test_api_qa.py -q`（离线 env） | 0 | **41 passed** in 19.88s |
| `pytest tests/test_ingest.py tests/test_compile.py tests/test_relate.py tests/test_ontology.py tests/test_consistency.py tests/test_doc_admin.py -q` | 0 | **99 passed** in 1.95s |
| `pytest tests/ -q`（离线 env） | 0 | **255 passed, 1 warning in 74.05s**（1 warning 为 starlette/httpx 上游弃用提示，已知登记） |

## Network Isolation

- 全程无真实 LLM/Embedding Key；Key/URL 通过命令行内联 `env -u` 仅对被测子进程清除，父进程环境未修改、值未打印；
- 测试进程设黑洞代理 + `NO_PROXY`；TestClient 走本地 ASGI；
- worktree 根目录无 `.env`（已核实），测试未读取用户真实 `.env`；
- 未发生真实网络访问（完整 pytest 在黑洞代理下全绿即证）。

## Data Manifest

全部测试（Anaconda 与干净 venv 两轮完整 pytest）前生成的 manifest（76 项）与测试后再生成的 manifest **完全一致**。`git status --short -- originals raw wiki meta`、`git diff -- originals raw wiki meta`、`git ls-files --others --exclude-standard -- originals raw wiki meta` 均为空。

## CI Gate

`.github/workflows/ci.yml` 的 `python-core`（job 名、timeout 20 分钟不变）在 `Run deterministic core tests` 之后新增 `Run full backend test suite`：env 清空四个 Key/URL、黑洞代理、`NO_PROXY`、`PYTHONUTF8=1`，执行 `python -m pytest tests/ -q`。前置分层步骤（install、pip check、compileall、startup smoke、offline retrieval、99 项核心）全部保留用于快速定位；未使用 `continue-on-error`/`|| true`；未配置真实密钥；`repository-integrity` 与 `frontend-unit-build` 未改。PR 三项 required checks 结果以 GitHub 运行为准。

## Known Warnings

- Anaconda 全局环境 `pkg_resources` namespace 弃用 warning（7 项，非项目包，与仓库无关）；
- 干净 venv 完整 pytest 1 项 warning：starlette/httpx TestClient 上游弃用提示（E001 已登记观察）；
- `pytest-asyncio` 声明但当前未使用（E001 登记观察，本任务不处理）；
- Anaconda base env `pip check` 噪声（非项目包冲突），权威 `pip check` 以干净 venv 与 CI 为准。

## Follow-up

- E003：多轮引用全局状态（下一阶段任务，本任务未开始）；
- 本任务不处理双后端统一、共享 YAML 并发、文档 ID 等其他已登记技术债；
- 后续任务可直接以完整 pytest 绿色作为后端验收前提。
