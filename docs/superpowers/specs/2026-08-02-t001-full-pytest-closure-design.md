# T001 Full Offline Pytest Closure Design

> Date: 2026-08-02 | Task: T001 | Base: `origin/dev` @ `f171aebdd975d10c70f3de76a38d71272a342027`

## Decision

- 只修测试隔离，不改生产代码（`api/`、`app/`、`scripts/` 零变更）；
- 成功路径同时 mock 客户端创建（`api.main.get_llm_client`）与一致性检查（`api.main.run_consistency_check`）；
- 检查失败路径也必须先让客户端创建成功，使异常确定性地来自 `run_consistency_check`，而不是无 Key 时 `get_llm_client` 提前失败；
- 完整 `python -m pytest tests/ -q` 成为 `python-core` 最终后端门禁；
- 现有分层测试步骤（startup smoke、offline retrieval、99 项确定性核心测试）保留，用于快速定位失败；
- CI 测试条件：无 Key、无 `.env`、黑洞代理（`HTTP(S)_PROXY=http://127.0.0.1:9`）、`PYTHONUTF8=1`。

## Current Failure

E002 合并后完整 pytest 基线（T001 会话新鲜复现，无 Key、无 `.env`、黑洞代理环境，Anaconda Python 3.12.7）：

- 命令：`python -m pytest tests/test_api.py::test_consistency_post_triggers_check -q`
  - exit 1；`AssertionError: assert 'error' == 'success'`（`tests/test_api.py:339`）；
  - 端点按既有设计降级返回 `status=error`，因为 `get_llm_client()` 在无 `OPENAI_API_KEY` 时先抛 `RuntimeError`（`scripts/search.py:48`），mock 的 `run_consistency_check` 从未被执行。
- 命令：`python -m pytest tests/ -q`
  - exit 1；**1 failed, 254 passed, 7 warnings in 73.44s**；
  - 唯一失败即上述测试，与 E001 登记、E002 复核的测试隔离债务一致；无其他未知失败。
- 数据目录 `originals/`、`raw/`、`wiki/`、`meta/` manifest（path|size|mtime_ns，76 项）执行前后完全一致；Git 数据目录无变化。

同时核实：`test_consistency_post_degrades_on_error` 当前只 mock 了 `run_consistency_check`（side_effect）；无 Key 环境下其 `status=error` 实际来自 `get_llm_client` 提前失败，mock 的 side_effect 从未触发——该测试是对其声明意图（"一致性检查过程失败 → 降级"）的假阳性，必须一并修正。

## Endpoint Contract

`POST /api/v1/consistency`（`api/main.py:659-678`）生产契约，本任务**不改变**：

| 场景 | 行为 |
|---|---|
| client 创建成功 + 一致性检查成功 | HTTP 200，`status=success`，返回报告字段 |
| client 创建成功 + 一致性检查抛异常 | HTTP 200，`status=error`，`total=0`，`contradictions=[]`，`message` 含异常信息 |
| client 创建失败（如无 Key） | HTTP 200，`status=error`，`total=0`（同上降级分支） |

HTTP 始终返回 200，业务结果由 `status` 字段区分 `success`/`error`。

调用顺序：`get_llm_client()` → `os.environ.get("RELATE_MODEL", os.environ.get("SEARCH_MODEL", "gpt-4o"))` → `run_consistency_check(client, model)`；整个块在 `try/except Exception` 内，异常走降级返回。

## Test Isolation Design

patch 边界：`api.main.get_llm_client` 与 `api.main.run_consistency_check`（两者均被 `api/main.py` 以模块级符号导入，patch `api.main.*` 即生效）。

1. `test_consistency_post_triggers_check`（success 路径）：
   - `@patch("api.main.get_llm_client")` 返回明确命名的 `MagicMock(name="consistency_llm_client")` fake client；
   - `monkeypatch.setenv("RELATE_MODEL", "test-relate-model")` 固定模型取值，消除对真实环境变量的依赖；
   - `@patch("api.main.run_consistency_check")` 返回确定性报告 dict；
   - 断言：HTTP 200、`status=success`、`total=1`、`contradictions[0].conflict_point`；
   - 调用断言：`mock_get_client.assert_called_once_with()`、`mock_run.assert_called_once_with(fake_client, "test-relate-model")`——证明请求真正到达被 mock 的一致性检查，且使用端点确定的 client 与 model。
2. `test_consistency_post_degrades_on_error`（检查异常路径）：
   - 同样 mock `get_llm_client` 成功并固定 `RELATE_MODEL`；
   - `run_consistency_check` 以 `side_effect=Exception("LLM down")` 抛异常；
   - 断言：HTTP 200、`status=error`、`total=0`；
   - 调用断言同上——证明 error 来自一致性检查过程本身，而非无 Key 提前失败，消除当前假阳性。

两测试均不设置真实 Key、不修改端点、不使用 skip/xfail、不放宽既有断言、不使用不受约束的 MagicMock 链条作为接口成功证据。`MagicMock` 已在 `tests/test_api.py` 导入，无新增依赖。

## CI Design

`.github/workflows/ci.yml` 的 `python-core`（job 名、timeout 20 分钟不变）在 `Run deterministic core tests` 之后新增最终步骤：

```yaml
      - name: Run full backend test suite
        env:
          OPENAI_API_KEY: ""
          EMBEDDING_API_KEY: ""
          OPENAI_BASE_URL: ""
          EMBEDDING_BASE_URL: ""
          HTTP_PROXY: "http://127.0.0.1:9"
          HTTPS_PROXY: "http://127.0.0.1:9"
          NO_PROXY: "localhost,127.0.0.1"
          PYTHONUTF8: "1"
        run: python -m pytest tests/ -q
```

关系：

- 前置分层步骤（startup smoke → offline retrieval → 99 项核心）保留，失败时快速定位到子集；
- 完整测试是最终后端门禁，覆盖分层子集之外的全部测试文件（含 `tests/test_api.py` 等）；
- 不使用 `continue-on-error`、`|| true`；不配置真实密钥；不删除任何已有检查；`repository-integrity` 与 `frontend-unit-build` 不变。

## Security and Isolation

- 测试不设置、不读取真实 LLM/Embedding Key；环境通过命令行内联 `env -u` 临时清除，父进程环境不被修改、值不被打印；
- 黑洞代理 + `NO_PROXY=localhost,127.0.0.1` 阻断任何意外外联（TestClient 走本地 ASGI，不受影响）；
- worktree 根目录无 `.env`（已核实），测试不读取用户真实 `.env`；
- 测试前后对 `originals/`、`raw/`、`wiki/`、`meta/` 生成 manifest（path|size|mtime_ns）比对，必须一致；Git 数据目录状态必须为空；
- 全部验证在独立 worktree `port-knowledge-base-t001` 与仓库外临时 venv 中执行，不触碰 G000 宿主与"知识库研究"worktree。

## File Scope

允许创建/修改（共 7 个）：

- `tests/test_api.py`（仅两个一致性 POST 测试）
- `.github/workflows/ci.yml`（仅 `python-core` 新增最终步骤）
- `docs/dev/tasks/T001-full-pytest-closure.md`（新建）
- `docs/superpowers/specs/2026-08-02-t001-full-pytest-closure-design.md`（本文件，新建）
- `docs/superpowers/plans/2026-08-02-t001-full-pytest-closure-implementation.md`（新建）
- `CLAUDE.md`（仅 §17 最小更新）
- `docs/dev/GIT_WORKFLOW.md`（仅 python-core 事实更新）

特别禁止：`api/`、`app/`、`scripts/`、`tests/conftest.py`、其他测试文件、`requirements.txt`、`frontend/`、数据目录、`.env`、`.env.example`、`ANTIGRAVITY.md`、`.claude/settings.json`。

## Non-goals

- 不修改任何生产代码；端点降级契约保持不变；
- 不统一 `api/main.py` 与 `app/` 双后端；
- 不处理共享 YAML 并发、文档 ID、多轮引用全局状态等其他已登记风险；
- 不开始 E003；
- 不把完整 pytest 绿色解释为前端 E2E 或真实栈 UAT 已完成；
- 不新增依赖、不修改 `requirements.txt`。

## Acceptance Criteria

1. 无 Key、无 `.env`、黑洞代理环境下，`tests/test_api.py` 全部通过；
2. 同一环境下完整 `python -m pytest tests/ -q` exit 0、failed=0、error=0；
3. 数据目录 manifest 前后一致，Git 数据目录无变化；
4. 仓库外干净 venv（Python 3.12）install + `pip check` + 完整 pytest 全绿；
5. CI `python-core` 包含 `Run full backend test suite` 步骤且三项 required checks 全绿；
6. Codex 审查当前 Head，无有效 P0/P1/P2 未处理；
7. diff 只含 7 个允许文件，生产代码零变更。

## Follow-up

- 下一阶段才是 E003（多轮引用全局状态），本任务不开始；
- 本任务不处理双后端统一、共享 YAML 并发、文档 ID 等其他技术债；
- CI 完整门禁建立后，后续任务可直接依赖完整 pytest 绿色作为后端验收前提。
