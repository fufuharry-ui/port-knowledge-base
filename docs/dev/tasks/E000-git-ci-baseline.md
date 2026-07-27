# E000 Git Baseline and CI Gate

## Status

- Task: E000
- Base: dev
- Feature branch: chore/e000-git-ci-baseline
- Risk: infrastructure
- Production code changes: none

## Problem

- 仓库默认分支仍可能指向孤立main；
- main、dev、master历史不统一；
- 缺少GitHub CLI统一操作通道；
- dev缺少自动化合并门禁；
- 全量后端测试仍存在Embedding和依赖方面的已知债务。

## Goal

- dev成为正式默认分支和当前工程集成基线；
- main/master保留为隔离历史，不做重写；
- 建立PR模板；
- 建立GitHub Actions初始门禁；
- 初始required checks为：
  - repository-integrity
  - python-core
  - frontend-unit-build
- Claude Code能够使用gh创建、检查和合并PR。

## Non-goals

- 不重写main/master/dev历史；
- 不修复Embedding降级；
- 不修复requirements缺项；
- 不统一双后端；
- 不修改生产代码；
- 不强制完整pytest在E000绿色；
- 不要求仓库作者批准自己的PR。

## Allowed Files

- `.github/workflows/ci.yml`
- `.github/pull_request_template.md`
- `docs/dev/GIT_WORKFLOW.md`
- `docs/dev/tasks/E000-git-ci-baseline.md`
- `CLAUDE.md`

## Validation

本地验证（在E000 worktree中执行）：

- `python -m pip install -r requirements.txt`
- `python -m compileall -q api app scripts tests`
- `python -m pytest tests/test_ingest.py tests/test_compile.py tests/test_relate.py tests/test_ontology.py tests/test_consistency.py tests/test_doc_admin.py -q`
- `cd frontend && npm ci`
- `npm test -- --runInBand`
- `npm run build`
- `git diff --check`
- `git status --short`（确认只有五个允许文件变化）
- workflow YAML可解析性检查

GitHub检查（PR创建后）：

- 三个初始required-check候选全部绿色：
  - repository-integrity
  - python-core
  - frontend-unit-build

已知审计项：`npm run lint` 当前不作为required gate；如失败，仅登记为后续债务，不在E000处理。

## Follow-up

- E001：依赖、干净环境启动和完整后端测试；
- E002：Embedding可选化及真正离线测试；
- E003：多轮引用状态；
- 后续CI再逐步提升，不得永久停留在初始子集。
