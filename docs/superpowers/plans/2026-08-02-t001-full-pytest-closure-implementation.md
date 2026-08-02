# T001 Full Offline Pytest Closure — Implementation Plan

> Date: 2026-08-02 | Task: T001 | Design: `docs/superpowers/specs/2026-08-02-t001-full-pytest-closure-design.md`
> Base: `origin/dev` @ `f171aebdd975d10c70f3de76a38d71272a342027` | Branch: `chore/t001-full-pytest-closure`

统一测试环境（所有 pytest 命令前缀，内联设置、不修改父进程环境、不打印原值）：

```bash
env -u OPENAI_API_KEY -u EMBEDDING_API_KEY -u OPENAI_BASE_URL -u EMBEDDING_BASE_URL \
  PYTHONUTF8=1 HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \
  NO_PROXY=localhost,127.0.0.1 python -m pytest ...
```

## 1. RED 复现（已完成，证据见设计文档 Current Failure）

- [x] 确认 worktree 无 `.env`；
- [x] 生成数据目录 manifest（76 项）；
- [x] `pytest tests/test_api.py::test_consistency_post_triggers_check -q` → **exit 1**，`assert 'error' == 'success'`（test_api.py:339）；
- [x] `pytest tests/ -q` → **exit 1，1 failed / 254 passed / 7 warnings**（唯一失败为目标测试）；
- [x] manifest 前后一致；Git 数据目录无变化。

## 2. success 测试 mock 修正

- [ ] 文件：`tests/test_api.py`（仅 `test_consistency_post_triggers_check`）；
- [ ] 增加 `@patch("api.main.get_llm_client")`（内层装饰器，注意参数顺序：mock_run 在前、mock_get_client 在后）；fake client 为 `MagicMock(name="consistency_llm_client")`；`monkeypatch.setenv("RELATE_MODEL", "test-relate-model")`；
- [ ] 断言保留 success/total/conflict_point，新增 `mock_get_client.assert_called_once_with()` 与 `mock_run.assert_called_once_with(fake_client, "test-relate-model")`；
- [ ] 预期：该测试由 RED 转 GREEN。

## 3. error 测试 mock 修正

- [ ] 文件：`tests/test_api.py`（仅 `test_consistency_post_degrades_on_error`）；
- [ ] 增加 `@patch("api.main.get_llm_client")` 成功返回 fake client；`monkeypatch.setenv("RELATE_MODEL", "test-relate-model")`；`run_consistency_check` 保持 `side_effect=Exception("LLM down")`；
- [ ] 断言保留 status=error/total=0，新增两条调用断言（证明 error 来自检查过程而非无 Key 提前失败，消除假阳性）；
- [ ] 预期：该测试保持 GREEN 且语义真实。

## 4. 目标测试 GREEN

- [ ] `pytest tests/test_api.py::test_consistency_post_triggers_check tests/test_api.py::test_consistency_post_degrades_on_error -q` → exit 0，2 passed；
- [ ] `pytest tests/test_api.py -q` → exit 0，全部通过。

## 5. 完整 pytest GREEN

- [ ] `pytest tests/ -q` → exit 0，failed=0，error=0；记录实际 passed 与 warning 数；
- [ ] 数据 manifest 前后一致；`git status/diff/ls-files -- originals raw wiki meta` 为空。

## 6. CI 完整门禁

- [ ] 文件：`.github/workflows/ci.yml`；
- [ ] `python-core` 在 `Run deterministic core tests` 之后新增 `Run full backend test suite`（env 清空四个 Key/URL + 黑洞代理 + NO_PROXY + PYTHONUTF8=1；`run: python -m pytest tests/ -q`）；
- [ ] 不改 job 名、不改 timeout、不动其他两个 job、不加 continue-on-error/真实密钥。

## 7. 干净 venv（仓库外 `$TEMP\port-kb-t001-clean-venv`，Python 3.12 优先）

- [ ] `python -m venv` 创建；`pip install -r requirements.txt` exit 0（GBK 问题按预案仅在 venv 内升级 pip）；
- [ ] `pip check` exit 0；`compileall -q api app scripts tests` exit 0；
- [ ] startup smoke → exit 0；offline retrieval 四文件 → exit 0；99 项核心 → exit 0；
- [ ] 完整 `pytest tests/ -q` → exit 0；
- [ ] 验证完成后删除 venv。

## 8. 文档更新

- [ ] 新建 `docs/dev/tasks/T001-full-pytest-closure.md`（State: IMPLEMENTED_AND_VALIDATED，不虚构 merge SHA）；
- [ ] `CLAUDE.md` §17：一致性测试 LLM mock 隔离债务标记 T001 已处理；无 Key 完整 pytest 全绿；风险 11 更新为 python-core 已含完整后端 pytest 最终门禁；Version/Updated 最小更新；
- [ ] `docs/dev/GIT_WORKFLOW.md`：python-core 步骤事实更新（新增完整 pytest 最终门禁；完整绿色≠前端/真实栈 UAT 完成）。

## 9. PR、Codex、合并、清理

- [ ] Commit 1：`docs: design T001 full pytest closure`（设计 + 计划）；
- [ ] Commit 2：`test: isolate consistency endpoint LLM dependencies`（tests/test_api.py）；
- [ ] Commit 3：`ci: gate complete offline backend pytest`（ci.yml + T001 任务文档 + CLAUDE.md + GIT_WORKFLOW.md）；
- [ ] 每次提交前 `git diff --cached --check/--name-only/--stat`；最终 diff 只含 7 个允许文件；
- [ ] `git push -u origin chore/t001-full-pytest-closure`；创建非 Draft PR（base=dev）；
- [ ] 等待三项 required checks 全绿，核实 python-core 日志含 `Run full backend test suite` 通过；
- [ ] 评论 `@codex review`，等待对当前 Head 的审查结论；逐条验证处理；最多 3 轮；
- [ ] 合并前重新读取 headRefOid / base / mergeStateStatus / threads；`gh pr merge --merge --match-head-commit <sha>`；
- [ ] 合并后验证 merge SHA 为 origin/dev 祖先，等待 dev push CI 三项全绿；
- [ ] 删除远程分支、移除 worktree、`git worktree prune`、`git branch -d`；
- [ ] 输出最终报告（T001_COMPLETE_AND_MERGED 或 T001_BLOCKED_<原因>），停止，不开始 E003。
