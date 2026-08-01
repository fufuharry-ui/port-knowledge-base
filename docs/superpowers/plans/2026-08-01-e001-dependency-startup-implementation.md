# E001 Dependency and Startup Baseline Implementation Plan

> Task: E001 | Base: `origin/dev` @ `8c183a03ba2172193bb82d6bf07a9256663d14af` | Date: 2026-08-01
> Branch: `chore/e001-dependency-startup-baseline` | Role: 实施工程师 (Kimi K3)
> Design: `docs/superpowers/specs/2026-07-28-e001-dependency-startup-design.md` (方案A已批准)

---

## 1. requirements.txt 修改范围

在现有 11 条声明基础上新增 4 行（含简短注释），其余行不动、不改版本下限：

```text
requests>=2.31.0        # scripts/embedding_client.py 顶层 import
sse-starlette>=2.1.0    # api/main.py 顶层 import（SSE 端点）
pydantic>=2.7.0         # api/main.py 与 app/schemas.py 直接 import
pytest>=8.2.0           # tests/ 24 个文件直接 import
```

原则：只用 `>=` 下限，不打钉版；兼容 CI Python 3.11 与本地 Python 3.12；若与 fastapi 解析冲突，提高该条下限解决，不降低 `fastapi>=0.111.0`；`pytest-asyncio` 保留并在报告中记录"声明但未使用"。

## 2. tests/test_startup_smoke.py 设计

新建文件，父进程只用标准库 + pytest，不向父进程 import 任何 `api`/`app`/`scripts` 模块：

- `_copy_source_snapshot(tmp_path)`：只复制 `api/`、`app/`、`scripts/` 到快照；创建空骨架 `raw/`、`wiki/`、`meta/ontology/`、`meta/relations/`、`originals/`；写最小 `wiki/index.yaml`（`documents: []`）；不复制 `.env`、`.git`、真实数据目录、`tests/`、`frontend/`；
- `_child_env(snapshot)`：删除 `OPENAI_API_KEY`、`EMBEDDING_API_KEY`、`OPENAI_BASE_URL`、`EMBEDDING_BASE_URL`；设置 `PYTHONUTF8=1`、黑洞代理 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:9`、`PYTHONPATH=快照根`；
- `_CHILD_SCRIPT`：子进程内联脚本，依次 `import scripts.embedding_client`、`import api.main`、`import app.main`，用 `TestClient` 请求 `/api/v1/health` 与 `/health`，把状态码与 body 以 JSON 打印到 stdout；
- 三个用例：`test_embedding_client_importable_without_keys`、`test_api_main_health_ok_without_env`、`test_app_main_health_ok_without_env`，各自独立 `tmp_path`；
- 父进程断言：exit code 0、响应 200 且 `status=="ok"`、stderr 无 API Key 异常痕迹；
- 使用 `sys.executable` 启动子进程；不 mock 任何生产模块。

## 3. CI python-core 扩展

只修改 `.github/workflows/ci.yml` 的 `python-core` job，不新增 job、不更名：

- `Install dependencies` 之后插入 `Verify installed dependencies: python -m pip check`；
- `Compile Python sources` 之后、`Run deterministic core tests` 之前插入 `Run startup smoke tests: python -m pytest tests/test_startup_smoke.py -q`；
- 保留 job 名 `python-core`、compileall、6 个核心测试步骤；`repository-integrity` 与 `frontend-unit-build` 不动；
- 不引入 secret、不用 `continue-on-error`、不用 `|| true`。

## 4. Clean venv 验证

两层验证：

- 层1（权威门禁）：GitHub Actions 干净 runner 上的 `python-core` 全流程；
- 层2（本地交叉验证）：`$env:TEMP\e001-clean-venv`（Python 3.12，仓库外），依次 `pip install -r requirements.txt` → `pip check` → `pytest tests/test_startup_smoke.py -q` → 6 文件核心套件（99 项）；venv 中不创建 `.env`；验证后删除 venv。

## 5. 完整 pytest 审计策略

在无 `.env`、无密钥的本地干净环境执行一次 `python -m pytest tests/ -q`：

- 记录 exit code 与 passed/failed/skipped 计数；
- 按根因分类：Embedding 缺失类（登记 E002，不阻断）/ 依赖或启动类（E001 范围，必须修复）/ 其他类（如实记录为后续债务）；
- 不 mock、不 skip、不 xfail 强行变绿；不隐藏非零 exit code；
- 审计结果原文写入 `docs/dev/tasks/E001-dependency-startup-baseline.md`。

## 6. Codex Review 流程

- PR 创建后等待三个 required checks（repository-integrity / python-core / frontend-unit-build）绿色；
- PR 评论 `@codex review`；
- P0/P1 必须修复；P2 涉及正确性/安全/测试隔离则修复；P3 记录即可；
- 修复后重新等待 CI 与 Codex 复审。

## 7. PR 和 merge 流程

- 分阶段 commit（不用 `git add .` / `git commit -a`，只 add 明确文件）：
  - commit1：test/docs（requirements、冒烟测试、任务文档、计划文档、CLAUDE.md）
  - commit2：ci（ci.yml）
- `git push -u origin chore/e001-dependency-startup-baseline`；
- `gh pr create`，base=`dev`，title=`chore: establish E001 dependency and startup baseline`；
- 合并条件：CI 绿色 + Codex 完成当前 Head 审查 + review thread 全部解决 + PR CLEAN + base=dev + 重新读取 headRefOid；
- `gh pr merge <PR> --merge --match-head-commit <最新HeadSHA>`；禁止 `--admin`/`--squash`/`--rebase`/force push。

## 8. 明确不修改

- `api/`、`app/`、`scripts/`（生产代码零变更）；
- `tests/conftest.py`、`tests/test_search.py` 及所有现有测试；
- `.env`、`.env.example`；
- `frontend/`、`package.json`、`package-lock.json`；
- `raw/`、`wiki/`、`meta/`、`originals/` 真实知识数据；
- Embedding 逻辑（E002）、双后端统一、CORS、文档 ID 生成。

## 允许修改文件清单

1. `requirements.txt`
2. `tests/test_startup_smoke.py`（新建）
3. `.github/workflows/ci.yml`
4. `docs/dev/tasks/E001-dependency-startup-baseline.md`（新建）
5. `CLAUDE.md`（仅 §17 第 2、4 条）
6. 本计划文档 `docs/superpowers/plans/2026-08-01-e001-dependency-startup-implementation.md`（新建）

## 无人值守执行边界

允许继续：LOW 问题、MEDIUM 文档问题、ESLint/npm audit 已有问题、pytest-asyncio 观察记录、Codex P3 建议。
必须停止：需修改生产代码、真实数据变化、Git 冲突、base 分支漂移、force push 需求、CI 无法解释失败、Codex P0/P1/P2 问题。

## 检查点

每完成一个阶段写 `$env:TEMP\port-kb-e001-checkpoint.md`：时间、阶段、HEAD、分支、PR、CI 状态、Codex 状态、下一步。
