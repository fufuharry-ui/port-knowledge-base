# E003 消息级引用与请求生命周期隔离 — 任务事实记录

> Date: 2026-08-03 | Task: E003 | 状态: 本地实施完成（三个绿色提交），等待用户审查；PR/CI/Codex/merge 未执行

## 1. 任务身份

- Base: `origin/dev` @ `3408b41ad0eacfd99bdb1c8352581e217dbbac6e`（实施期间 merge-base 复核一致，无漂移）
- Branch: `fix/e003-message-scoped-citations`
- Worktree: `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e003`
- 设计规格: `docs/superpowers/specs/2026-08-02-e003-message-scoped-citations-design.md`
- 实施计划: `docs/superpowers/plans/2026-08-02-e003-message-scoped-citations-implementation.md`
- 执行范围: 仅获批计划 Task 0—Task 3；Task 4（PR/CI/Codex/合并/清理）未执行
- Python 解释器: `D:\ProgramData\anaconda3\python.exe`（Python 3.12.7；PATH 中的 `python` 为 WindowsApps stub，未使用）

## 2. 问题与根因

- `messages` 只保存 `{id, role, content}`，`citations` 是 ChatPanel 组件级全局状态；第二轮 `source` 事件覆盖全局引用，渲染时所有 assistant 消息共享同一份 citations，导致历史消息正文中的 `[1]` 被映射到最新一轮的文档；新一轮 `setCitations([])` 还会使历史引用标记在新 source 到达前全部失效。
- 根因：引用的生命周期属于单条 assistant 消息，却被存放在组件级状态（前端状态所有权错误）。
- 设计审查追加发现的两个竞态：点击停止到 AbortError 进入 catch 之间迟到事件仍可写入（React `setMessages` 异步，单靠消息 status 无法覆盖该窗口）；旧请求 `finally` 无条件清理全局控制器引用与 `isStreaming`，可破坏随后启动的新请求。

## 3. Task 0：Preflight 事实

- Git 基线：分支正确、工作区 clean、HEAD=`da3db18`、origin/dev 无漂移；
- before manifest：`$TEMP\port-kb-e003-data-before.json`，覆盖 `originals/`、`raw/`、`wiki/`、`meta/` 共 **76 个文件**（字段 `relative_path | size | mtime_ns`，写于仓库外）；
- Git 数据目录基线：`git status --short` / `git diff --name-only` / `git diff --cached --name-only` 全空；`git ls-files originals raw wiki meta` = 76 个已跟踪文件；
- `npm ci`：exit 0；
- 基线 Jest：15 suites / **100 tests passed**，exit 0。

## 4. Task 1：多轮引用隔离（RED → GREEN → 绿色提交）

### RED（新鲜运行，未提交）

命令：`npx jest tests/unit/ChatPanel.test.tsx --runInBand`（frontend 目录）→ exit 1，3 failed / 2 passed：

- **T1.3 FAIL**：`Unable to find an element by: [data-testid="citation-link-doc_A"]` —— 第二轮后第一条正文中的 `[1]` 被全局 citations（DOC_B）错误映射，hover 出现的是 doc_B 链接；
- **T1.4 FAIL**：`Unable to find an element by: [data-testid="citation-trigger"]`（第一条气泡内）—— 第二轮开始 `setCitations([])` 清空全部历史引用；
- **T1.5 FAIL**：`Unable to find an element by: [data-testid="citation-link-doc_A"]` —— 同一缺陷的另一表现；
- T1.1、T1.2 PASS（现状行为回归锁定，未冒充 RED）。

测试环境修正（不属于 RED）：jsdom 无 `scrollIntoView`（ThoughtTrace effect 调用）与 `ResizeObserver`（Radix Tooltip use-size），在测试文件 `beforeAll` stub；jsdom 不触发 `animationend`，Radix Tooltip 退场副本不卸载，链接断言统一 `findAllByTestId` 取首个。

### 实现

- `Message` 改为可辨识联合：`UserMessage` / `AssistantMessage`（携带 `citations: CitationMeta[]` 与 `status: AssistantStatus`）；
- 删除组件级 `citations` state 与新轮 `setCitations([])`；
- 新增 `updateAssistant`（`useCallback(..., [])`）与 `updateStreamingAssistant`（`useCallback(..., [updateAssistant])`，仅 `status === 'streaming'` 时应用更新）；
- `source`/`delta`/`done` 事件经 `updateStreamingAssistant` 定向写入目标消息；
- 渲染 `citations={msg.role === 'assistant' ? msg.citations : []}`；
- history 收集不变（仍只映射 `role`/`content`）。

### GREEN 与提交

- 目标测试：5/5 passed，exit 0；
- 全部 Jest：16 suites / **105 tests passed**，exit 0；
- Commit：`34b5874` `fix: scope citations to assistant messages`（ChatPanel.tsx 与 ChatPanel.test.tsx 一并提交）。

### 相同 [1] 独立映射证据

两轮引用均使用 `ref: '[1]'`（DOC_A→doc_A、DOC_B→doc_B）。T1.3/T1.5 自动化证据：第一条 assistant 气泡的 `[1]` hover 后 `citation-link-doc_A` href=`/wiki/doc_A`；第二条的 `[1]` hover 后 `citation-link-doc_B` href=`/wiki/doc_B`；T1.4 证明第二轮 source 到达前第一条引用不消失。

## 5. Task 2：请求生命周期（RED → 最终实现 → 绿色提交）

### RED（新鲜运行，未提交）

命令同上 → exit 1，3 failed / 15 passed（共 18 项）：

- **T-R1 FAIL**：`Unable to find citation-link-doc_A` —— 点击停止后、AbortError 到达前，迟到的 `source` 事件仍覆盖了引用（`emitAndWaitForNextPull` 确认事件确被组件消费，非未到达的假绿）；
- **T-R2 FAIL**：`expect(chat-input).toBeEnabled()` 失败 —— 停止点击后 UI 未立即恢复（现状要等 catch/finally），且旧 finally 无条件 `setIsStreaming(false)` + 清空控制器引用会清理新请求状态；
- **T-R3 FAIL**：同上前置断言失败（停止后输入未立即恢复，无法在不修复的情况下进入后续场景）；
- 其余 15 项 PASS（现状行为回归锁定：T2.1/T2.2/T2.5/T2.6/T2.7/T2.8/T2.9/T2.10/T-R4/T2.11/T1.x）。

### T-R1 事件消费 ack 机制

`ControlledQAStream` 记录 async iterator 的 `next()` 调用次数（`pullCount`）；`emitAndWaitForNextPull(event)` 投递事件后等待 `pullCount` 增加。for-await 只有在上一次迭代体（含请求身份守卫）执行完毕后才会再次调用 `next()`，因此"再次 pull"证明前一事件已进入循环并被处理。五个迟到事件（thought/source/entity/delta/done）逐一经 ack 确认消费后才 `fail(abortError())`。

### 最终实现（一次性落最终形态，无错误中间提交）

- `ActiveRequest { assistantId, controller, stopped }` + `activeRequestRef`（替代 `abortRef`）；
- `isWritableRequest = useCallback((request) => activeRequestRef.current === request && !request.stopped, [])`；
- `handleStop` 固定顺序：`request.stopped = true`（严格先于 abort）→ 消息标记 `stopped`（保留 content/citations，追加一次 `⏹ *已停止生成*`）→ `setIsStreaming(false)` → `request.controller.abort()`；
- 事件循环顶部 `if (!isWritableRequest(request)) continue;` —— 五类事件统一身份守卫；`done` 仅标记 `completed` 并 `break` 退出循环，不直接触碰 `isStreaming`/ref；
- AbortError 三路径：显式停止（`stopped` 已 true）只吞掉不重复提示；当前请求非显式 AbortError（`activeRequestRef.current === request && !request.stopped`）执行一次幂等兜底停止；旧请求 AbortError（身份不匹配）完全忽略；
- 普通错误仅当前未停止请求允许标记 `error`（整体替换正文为 `` `⚠️ 流式请求失败: ${err}` `` 的现状产品行为不变，`...m` 展开保留已收到 citations）；
- `finally` 仅 `activeRequestRef.current === request` 时清理 ref 与 `isStreaming`；
- `handleSubmit` 依赖数组：`[inputValue, isStreaming, messages, onHighlight, scrollToBottom, updateStreamingAssistant, isWritableRequest]`；未禁用 `react-hooks/exhaustive-deps`；
- 停止按钮 `onClick={handleStop}`。

### 13 项生命周期测试结果（GREEN）

T2.1 source 后停止保留正文和引用；T2.2 source 前停止引用为空；T-R1 五类迟到事件全部被消费但全部忽略；T-R2 旧 finally 不清理新请求（第二轮 streaming/停止按钮/`s2.signal.aborted` 均验证）；T-R3 旧普通错误不污染新请求且不改写已停止消息；T-R4 正常 done 统一收口；T2.5 当前普通错误整体替换正文；T2.6 当前非显式 AbortError 幂等兜底一次；T2.7 stopped 正文进入下一轮 history 且字段仅 `role`/`content`（`Object.keys(turn).sort()` 断言）；T2.8 HTTP 错误 delta+done 组件契约（不声称覆盖 qa-stream 内部 HTTP 分支）；T2.9 有效 entity 调用 `onHighlight`；T2.10 流式交互门禁；T2.11 第二轮错误不覆盖第一轮引用。

普通错误后内部 citations 数组保留由 `...message` 展开结构保证，经代码审查与 TypeScript 类型确认；未为不可见内部状态增加生产测试接口。

### 验证与提交

- 目标测试：18/18 passed，exit 0；
- 全部 Jest：16 suites / **118 tests passed**，exit 0；
- touched-file ESLint：`npx eslint src/components/ChatPanel.tsx tests/unit/ChatPanel.test.tsx` → exit 0；
- Next build：`npm run build` → exit 0；
- Commit：`bb8f9b1` `fix: isolate stopped and stale chat requests`（ChatPanel.tsx 与 ChatPanel.test.tsx 一并提交）。

## 6. Task 3：完整仓库验证

- 离线完整 pytest（仓库根目录，`D:\ProgramData\anaconda3\python.exe -m pytest tests/ -q`，进程内临时环境：无 Key、空 Base URL、黑洞代理 `http://127.0.0.1:9`、`PYTHONUTF8=1`，finally 恢复原环境，未打印任何旧环境值）：
  - **255 passed, 0 failed, 0 errors, 7 warnings（均为第三方包 DeprecationWarning）, 90.10s, exit 0**；
  - 未访问真实外部服务（黑洞代理 + 空 Key，套件按离线设计全绿，与 T001 基线一致）；
- after manifest：`$TEMP\port-kb-e003-data-after.json`，与 before 比较结果 **MANIFEST_MATCH**（76 文件 path/size/mtime_ns 完全一致）——数据目录零污染；
- Git 范围核验（文档提交前）：`git status --short` clean、`git diff --check` 无输出、`git diff --name-status origin/dev` 仅允许文件、`git diff --cached --name-status` 为空。

## 7. 范围核验（允许文件清单）

分支相对 origin/dev 的变更仅为：

- `docs/superpowers/specs/2026-08-02-e003-message-scoped-citations-design.md`（设计，先前阶段）
- `docs/superpowers/plans/2026-08-02-e003-message-scoped-citations-implementation.md`（计划，先前阶段）
- `frontend/src/components/ChatPanel.tsx`（本任务唯一生产代码）
- `frontend/tests/unit/ChatPanel.test.tsx`（本任务新建测试）
- `CLAUDE.md`（§11 与 §17 第 8 项最小更新）
- `docs/dev/tasks/E003-message-scoped-citations.md`（本文件）

未修改：ChatBubble.tsx、ThoughtTrace.tsx、qa-stream.ts、package.json、package-lock.json、Jest 配置、CI、后端 Python 文件、数据目录、Playwright 文件。

## 8. 未执行事项

- Live UAT（真实栈）未执行；
- 历史 thoughts 仍未持久化（Non-goal 未变，thoughts 保持当前轮临时状态）；
- PR / required checks / Codex / merge / 分支与 worktree 清理均未执行（实施 commit 未 push）；
- 最终 PR 和完成报告为这些后续事实的权威来源。
