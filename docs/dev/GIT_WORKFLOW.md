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
- Codex GitHub Review独立审查；
- Claude Code执行合并；
- 合并后清理任务worktree和本地分支。

标准流程：

1. Kimi实施；
2. 本地验证；
3. 创建PR（base=dev）；
4. required checks全部绿色；
5. PR评论 `@codex review` 触发Codex审查；
6. 验证并处理Codex意见（不得盲目执行，先验证再修复或技术性回复）；
7. 若Head变化：新Head重新跑required checks并重新触发Codex审查；
8. 所有review thread解决；
9. 使用 `--match-head-commit` 守护合并。

Codex意见分级处理：

- P0/P1以及经核实有效的正确性、安全性P2：阻断合并，必须修复；
- P3/LOW：经技术判断可登记后继续；
- Codex"无问题"结论必须对应当前Head；
- Codex不替代required checks；
- 不再要求GLM审查。

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

required checks为三个：

- **repository-integrity**：检查变更文件的空白错误（`git diff --check`），拒绝被跟踪的`.env`和生成产物（`node_modules`、`.next`、`.pytest_cache`、`__pycache__`、`test-results`、`.uat`等）；
- **python-core**：在干净环境中安装`requirements.txt`并执行`pip check`，编译全部Python源码，运行无密钥startup smoke（`tests/test_startup_smoke.py`），并运行确定性核心测试子集（`test_ingest`、`test_compile`、`test_relate`、`test_ontology`、`test_consistency`、`test_doc_admin`，当前99项）；
- **frontend-unit-build**：`npm ci`、Jest单元测试、Next.js生产构建。

同时明确：

- `python-core`不是完整后端验证；
- 不得把它描述为全量pytest（E001完整pytest审计为237通过/7失败，失败项已分类登记）；
- E001已完成依赖声明、`pip check`与startup smoke门禁扩展；真正离线检索及Embedding可选降级门禁待E002。

## GitHub CLI

常用命令：

```bash
gh auth status
gh pr create
gh pr view
gh pr checks --watch
```

受保护合并必须使用`--match-head-commit`守护。合并前必须重新读取PR的`headRefOid`，并把该SHA作为参数紧跟在`--match-head-commit`之后：

```powershell
$headSha = gh pr view <PR_NUMBER> `
  --repo fufuharry-ui/port-knowledge-base `
  --json headRefOid `
  --jq .headRefOid

gh pr merge <PR_NUMBER> `
  --repo fufuharry-ui/port-knowledge-base `
  --merge `
  --match-head-commit $headSha
```

约束：

- 必须在合并前重新读取`headRefOid`，不得复用旧SHA；
- `--match-head-commit`必须紧跟实际SHA参数，不能作为布尔开关使用；
- 读取到的Head与预期不一致时停止合并并重新审查；
- 禁止使用`--admin`绕过分支保护。

不得在文档中写入token或个人凭据。
