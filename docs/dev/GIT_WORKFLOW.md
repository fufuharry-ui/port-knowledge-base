# Git and Pull Request Workflow

## Active Branches

- `dev`：当前默认分支、集成分支、所有PR默认目标；
- `main`：孤立旧历史，不作为开发或PR目标；
- `master`：另一组旧历史，不作为开发或PR目标；
- `main`/`master`不得merge、rebase或force到`dev`；
- 历史统一必须另立高风险任务。

## Task Workflow

- 一个任务编号；
- 一个分支；
- 一个worktree；
- 一个PR；
- Kimi实施；
- GLM独立复审；
- Claude Code执行合并；
- 合并后清理任务worktree和本地分支。

## Branch Naming

- `feat/<task>-...`
- `fix/<task>-...`
- `chore/<task>-...`
- `docs/<task>-...`

## PR Rules

- PR base必须为`dev`；
- 禁止直接push `dev`；
- 禁止force push；
- 必须通过required checks；
- 中高风险任务必须取得独立复审APPROVE；
- merge method使用merge commit；
- 不使用rebase merge；
- squash仅由用户针对特定PR明确授权。

## Initial Required Checks

初始required checks为三个：

- **repository-integrity**：检查变更文件的空白错误（`git diff --check`），拒绝被跟踪的`.env`和生成产物（`node_modules`、`.next`、`.pytest_cache`、`__pycache__`、`test-results`、`.uat`等）；
- **python-core**：在干净环境中安装`requirements.txt`，编译全部Python源码，并运行确定性核心测试子集（`test_ingest`、`test_compile`、`test_relate`、`test_ontology`、`test_consistency`、`test_doc_admin`）；
- **frontend-unit-build**：`npm ci`、Jest单元测试、Next.js生产构建。

同时明确：

- 初始`python-core`不是完整后端验证；
- 不得把它描述为全量pytest；
- E001/E002完成后扩大门禁。

## GitHub CLI

常用命令：

```bash
gh auth status
gh pr create
gh pr view
gh pr checks --watch
gh pr merge --merge --match-head-commit
```

不得在文档中写入token或个人凭据。
