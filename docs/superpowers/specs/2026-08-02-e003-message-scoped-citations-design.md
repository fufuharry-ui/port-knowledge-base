# E003 Message-Scoped Citations and Generation Status Design

> Date: 2026-08-02 | Task: E003 | Phase: DESIGN ONLY

## 1. Status and Context

- Task: E003
- Phase: **DESIGN_APPROVED_AWAITING_SPEC_REVIEW**（产品选择与实现方案已获用户批准；本规格等待用户审阅，未开始实施）
- Base: `origin/dev` @ `3408b41ad0eacfd99bdb1c8352581e217dbbac6e`（与任务预期一致，无漂移；即 T001 合并提交）
- Branch: `fix/e003-message-scoped-citations`
- Worktree: `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e003`
- 风险类型： frontend-state / streaming-state
- 生产缺陷现状： 多轮问答中历史 assistant 消息的引用被最新一轮的全局 citations 覆盖（CLAUDE.md §11 与 §17 第 8 条已登记的已知缺陷，未修复）；
- 已批准的产品选择（不得重新打开，除非代码事实证明不可行）：
  1. 每条 assistant 消息独立保存 `content`、`citations`、`status`；思考轨迹仍是当前轮临时执行状态，不保存到历史消息；
  2. 停止生成采用方案 A：已收到 source 后停止 → 保留已有正文和引用；未收到 source 前停止 → 引用保持空数组；正文追加"⏹ 已停止生成"；status 变为 `stopped`；停止后迟到的 source、delta、done 不得继续修改消息；
  3. 实现采用方案 1：直接在 ChatPanel 内改为消息级状态；可辨识联合类型；小型按 assistantId 更新消息的辅助函数；不使用 useReducer；不抽取 useChatSession；不修改后端；不修改 SSE 协议；原则上不修改 ChatBubble 和 qa-stream。

## 2. Problem

当前缺陷（全部经 E003 worktree 内源码核实，行号见 §6）：

- `messages` 只保存 `{id, role, content}`，**不保存引用**（ChatPanel.tsx:10-14）；
- `citations` 是 ChatPanel **组件级全局状态**（ChatPanel.tsx:30）；
- 第二轮的 `source` 事件通过 `setCitations(event.citations)` **覆盖全局引用**（ChatPanel.tsx:99）；
- 渲染时**所有** assistant 消息接收同一份全局 citations（ChatPanel.tsx:197 `citations={msg.role === 'assistant' ? citations : []}`），历史消息用最新一轮的引用重新渲染；
- 后果一：第一轮正文中的 `[1]` 会被映射到第二轮的文档（`linkifyCitations` 按传入 citations 解析，ChatBubble.tsx:25-41），引用指向错误文档；
- 后果二：新一轮开始时 `setCitations([])`（ChatPanel.tsx:70）使所有历史 assistant 消息的引用标记在第二轮 source 到达前**暂时全部失效**（解析不到 meta 时退化为普通文本）；
- 后果三：停止生成后没有状态记录，迟到事件与历史消息的所有权边界不存在。

这是**前端状态所有权错误**：引用的生命周期属于单条 assistant 消息，却被存放在组件级状态。不是后端 SSE 协议错误（协议按轮次正确下发 source/delta/done），也不是 ChatBubble 引用解析错误（ChatBubble 完全按 props 传入的 citations 解析，本身无状态、无缺陷）。

## 3. Goals

- 每条 assistant 消息独立拥有正文、引用和生成状态（message-scoped）；
- 历史消息不受后续轮次 source/delta/done/error 影响；
- 停止后保留已收到的引用与正文（方案 A）；
- 停止后迟到的 source/delta/done 不可继续写入该消息；
- 多轮 history 协议保持 `{role, content}`（后端契约不变）；
- 当前轮 ThoughtTrace 行为保持（仅最新 assistant 消息前展示、流式结束隐藏逻辑不变）；
- 单轮问答与引用 Tooltip 无回归（ChatBubble 行为不变）；
- CLAUDE.md §11 的六条多轮问答验收全部可验证。

## 4. Non-goals

- 历史思考轨迹持久化；
- 思考轨迹折叠 UI；
- 重新生成（regenerate）；
- 编辑历史问题；
- 多请求并发（`isStreaming` 期间禁止发送的现状不变）；
- 会话数据库或 localStorage 持久化；
- 后端 SSE 协议修改；
- ChatBubble 引用格式重构；
- 图谱高亮历史回放；
- 错误提示视觉重构；
- 双后端统一（`api/` vs `app/`）；
- Live UAT（真实栈）。

## 5. Message Model

推荐的 TypeScript 模型（可辨识联合，`role` 为判别字段）：

```typescript
type AssistantStatus =
    | 'streaming'
    | 'completed'
    | 'stopped'
    | 'error';

interface UserMessage {
    id: string;
    role: 'user';
    content: string;
}

interface AssistantMessage {
    id: string;
    role: 'assistant';
    content: string;
    citations: CitationMeta[];
    status: AssistantStatus;
}

type Message = UserMessage | AssistantMessage;
```

要点：

- `status` 只存在于 assistant 消息；user 消息无生成状态；
- `citations` 随消息创建即初始化为 `[]`，不再存在组件级 `citations` state；
- 渲染处利用联合类型的判别收窄：`msg.role === 'assistant' ? msg.citations : []`，TypeScript 可直接推导出 `AssistantMessage`；
- history 收集代码（ChatPanel.tsx:63-66）对联合类型兼容：`role`/`content` 在两个分支上都存在，`.map(m => ({ role: m.role, content: m.content }))` 行为不变，发送给后端的协议不变（正文含停止标记的现状也不变）。

## 6. Current Code Facts（核验结论，E003 worktree 实际源码）

| # | 核验项 | 结论 | 证据 |
|---|---|---|---|
| 1 | Message 当前字段 | 只有 `id`、`role`、`content` | ChatPanel.tsx:10-14 |
| 2 | citations 是否全局状态 | 是，`useState<CitationMeta[]>([])` | ChatPanel.tsx:30 |
| 3 | 新轮开始是否清空 | 是，`setCitations([])` | ChatPanel.tsx:70 |
| 4 | source 事件处理 | 全局 `setCitations(event.citations)` | ChatPanel.tsx:99 |
| 5 | 所有 assistant 共享引用 | 是，渲染时统一传全局 citations | ChatPanel.tsx:197 |
| 6 | thoughts 展示范围 | 仅最后一条 assistant 消息前 | ChatPanel.tsx:188（`idx === messages.length - 1`） |
| 7 | abort 停止文本 | 追加 `⏹ *已停止生成*`（markdown 斜体形式，经 react-markdown 渲染为斜体） | ChatPanel.tsx:126-127 |
| 8 | AbortError 以外的异常 | **整体替换**正文为 `` `⚠️ 流式请求失败: ${err}` ``（不追加） | ChatPanel.tsx:128 |
| 9 | streamQA 能力 | 提供 thought/source/entity/delta/done 五类事件与 `AbortSignal` 参数；abort 时 reader 抛 AbortError | qa-stream.ts:13-18, 68-79 |
| 10 | ChatBubble 引用解析 | 完全按 props 的 citations 解析（`byRef`/`byTitle`/`byId` 均来自入参），无内部状态，可独立渲染每条消息的不同引用 | ChatBubble.tsx:25-41, 83-141 |
| 11 | Jest 中的 ChatPanel 测试 | **不存在**；全仓测试无任何 `ChatPanel`/`streamQA` 引用（grep 核实） | tests/unit/ 文件树 |
| 12 | Playwright qa 测试 | `tests/e2e/qa.spec.ts` 已冻结（`test.describe.skip`，面向废弃的图谱内嵌架构）；现行覆盖为 `tests/e2e/uat.spec.ts` UAT-F06（mock API，面向独立 /qa 页，不在 CI 运行）与 `tests/e2e-live/`（真实栈） | qa.spec.ts:9；uat.spec.ts:437-497 |
| 13 | frontend-unit-build 命令 | `npm ci` → `npm test -- --runInBand`（Jest）→ `npm run build`（Next.js 生产构建）；不运行 Playwright | .github/workflows/ci.yml:136-159 |
| 14 | React 19 / Next 16 约束 | Next 16.2.2 + React 19.2.4；ChatPanel 为 `'use client'` 组件，不使用任何 Next 服务端 API，本设计不涉及 Next 破坏性变化面；@testing-library/react 16.3.2 支持 React 19；jest 30 + ts-jest + jsdom；react-markdown v10 纯 ESM 由 `__mocks__/reactMarkdown.tsx` 替代（已支持平衡括号链接解析） | package.json；jest.config.js:19-20；frontend/AGENTS.md |

其他事实：

- 停止按钮仅在 `isStreaming` 时渲染（ChatPanel.tsx:226-237），`abortRef.current?.abort()` 触发；
- `isStreaming` 为组件级布尔，同一时刻只允许一个流式请求（`handleSubmit` 入口 `if (!query || isStreaming) return`，ChatPanel.tsx:53）——本设计保持该单并发前提；
- `streamQA` 在 HTTP 非 2xx 时以 `delta` + `done` 下发错误文本（qa-stream.ts:81-85），不抛异常——该路径下消息会收到错误文本 delta 并正常完成；
- 设计基线验证：E003 worktree 的 `frontend/node_modules` 不存在，按任务规定**未执行** `npm ci`，本阶段未运行 Jest/build；最新 CI 基线来自 T001 合并后 dev push CI（run 30737203234）`frontend-unit-build` success。不将此描述为本阶段已运行通过。

## 7. State Design

### 7.1 状态所有权迁移

| 状态 | 现状 | 设计后 |
|---|---|---|
| `messages` | `Message[]`（无引用/状态） | 可辨识联合 `Message[]`，assistant 携带 `citations` + `status` |
| `citations` | 组件级 `useState` | **删除**，迁入 `AssistantMessage.citations` |
| `thoughts` | 组件级 `useState` | **不变**（当前轮临时执行状态，不持久化） |
| `isStreaming` | 组件级 `useState` | **不变**（输入禁用、停止按钮、ThoughtTrace 光标、标题栏"推理中"都依赖它；单并发前提不变） |
| `abortRef` | `useRef<AbortController>` | **不变** |

### 7.2 status 状态机（每条 assistant 消息）

```text
创建占位 → streaming
streaming --done事件--> completed
streaming --AbortError--> stopped
streaming --其他异常----> error
completed / stopped / error 为终态：后续任何 source/delta/done/error 写入一律忽略
```

### 7.3 事件处理矩阵（handleSubmit 的 for-await 循环内）

| 事件 | 处理 |
|---|---|
| `thought` | `setThoughts` 追加（组件级，不变） |
| `source` | `updateStreamingAssistant(assistantId, m => ({...m, citations: event.citations}))` —— 写入该消息的 citations；消息已非 `streaming`（如已停止）时忽略 |
| `entity` | `onHighlight?.(event.ids)`（不变） |
| `delta` | `updateStreamingAssistant(assistantId, m => ({...m, content: m.content + event.text}))`；非 `streaming` 时忽略 |
| `done` | `updateStreamingAssistant(assistantId, m => ({...m, status: 'completed'}))`；`setIsStreaming(false)`（不变） |

### 7.4 异常处理（catch/finally）

- **AbortError**（方案 A）：对该消息执行终态更新——`content` 追加 `⏹ *已停止生成*`（保持现有 markdown 斜体文本，避免视觉回归；追加前有空行分隔的现状不变）、`status: 'stopped'`；`citations` 保持已收到值（未收到 source 则自然为 `[]`）；守卫：仅当消息仍为 `streaming` 时执行，保证重复 abort 或迟到异常不二次追加；
- **其他异常**：保持现有"整体替换正文为 `` `⚠️ 流式请求失败: ${err}` ``"的用户可观察行为（Non-goal：错误提示视觉重构），追加 `status: 'error'`；`citations` 保持已收到值；
- `finally`：`setIsStreaming(false)`、`abortRef.current = null`（不变）。

### 7.5 辅助函数（方案 1，不使用 useReducer）

```typescript
/** 按 id 更新 assistant 消息；非 assistant 或 id 不匹配时原样返回 */
const updateAssistant = (
    id: string,
    updater: (m: AssistantMessage) => AssistantMessage,
) => {
    setMessages(prev => prev.map(m =>
        m.id === id && m.role === 'assistant' ? updater(m) : m,
    ));
};

/** 仅当消息仍处于 streaming 时才应用更新——停止/完成/错误后的迟到事件一律丢弃 */
const updateStreamingAssistant = (
    id: string,
    updater: (m: AssistantMessage) => AssistantMessage,
) => {
    updateAssistant(id, m => (m.status === 'streaming' ? updater(m) : m));
};
```

所有流式写入（source/delta/done/异常终态）经 `updateStreamingAssistant`，从结构上保证"停止后迟到事件不可写入"（已批准方案 A 的最后一条）。

### 7.6 渲染变更

- ChatBubble 调用改为消息级：`citations={msg.role === 'assistant' ? msg.citations : []}`（替换 ChatPanel.tsx:197 的全局 citations）；
- ThoughtTrace 渲染条件不变（仅最后一条 assistant 消息前、组件级 thoughts）；
- `setCitations([])`（ChatPanel.tsx:70）与组件级 `citations` state 删除；新轮的"清空"通过创建 `citations: []` 的新消息自然达成，历史消息引用因此不再被清空；
- 占位消息创建为 `{ id: assistantId, role: 'assistant', content: '', citations: [], status: 'streaming' }`。

## 8. Implementation Organization（已批准方案 1 的落位）

- 唯一生产代码变更文件：`frontend/src/components/ChatPanel.tsx`；
- 不修改：`qa-stream.ts`（事件模型与 signal 已满足）、`ChatBubble.tsx`（已按 props 独立解析）、`ThoughtTrace.tsx`、`page.tsx`、后端、SSE 协议；
- 不使用 useReducer、不抽取 useChatSession；
- `CitationMeta` 类型继续从 `@/lib/qa-stream` 导入；
- 消息 id 生成保持 `user-${Date.now()}` / `assistant-${Date.now()}` 前缀形式（前缀不同，同毫秒不冲突；id 生成器重构不是本任务目标）。

## 9. Test Strategy（实施阶段执行，本阶段不创建）

新增 `frontend/tests/unit/ChatPanel.test.tsx`（Jest + @testing-library/react，`jest.mock('@/lib/qa-stream')` 提供可控 async generator），覆盖 CLAUDE.md §11 的六条验收：

1. 第一轮回答引用文档 A（source: docA → 该消息渲染 docA 的 citation-trigger）；
2. 第二轮回答引用文档 B（source: docB → 第二条消息渲染 docB）；
3. 第二轮结束后**第一条仍引用文档 A**（核心回归：docA 的 `citation-trigger`/`citation-link-doc_A` 仍存在且指向 docA，第一条中不存在 docB 链接）；
4. 停止生成不破坏已接收内容（source 后停止：正文保留、docA 引用保留、追加停止标记、status=stopped）；
5. 新请求不覆盖旧消息状态（第二轮 source 到达前，第一轮引用不消失——现状的 `setCitations([])` 缺陷回归守卫）；
6. 引用跳转仍指向对应文档（`citation-link-<doc_id>` 的 href 为 `/wiki/<doc_id>`，逐消息独立）。

另需覆盖：未收到 source 前停止 → 引用为空数组；停止后 mock 继续产出 delta/source/done → 消息不再变化（迟到事件丢弃）；非 abort 异常 → status=error 且错误文本行为不变；单轮问答与引用 Tooltip 回归（现有 ChatBubble.test.tsx 不修改、保持绿色）。

E2E 不在 CI 门禁内；`uat.spec.ts` F06 不修改（实施阶段如 mock 契约需要补充，另行评估，默认不动）。

## 10. Acceptance Criteria

1. §3 Goals 全部满足；
2. CLAUDE.md §11 六条多轮问答验收全部由自动化测试证明；
3. 现有 Jest 全部测试保持绿色（`npm test -- --runInBand`）；
4. `npm run build` 通过（Next 16 生产构建，含类型检查）；
5. 生产代码变更仅限 `ChatPanel.tsx`；后端、SSE 协议、ChatBubble、qa-stream 零变更；
6. CI `frontend-unit-build` 绿色；
7. 完整后端 pytest 绿色不受本任务影响（后端零变更）。

## 11. Risks and Follow-up

- **React state 闭包**：`handleSubmit` 依赖 `messages` 收集 history（现状不变）；事件写入全部经 `setMessages` 函数式更新，不读取闭包内 `messages`，无陈旧状态风险；
- **迟到事件窗口**：abort 后 for-await 退出前可能仍有已缓冲事件进入循环体——由 `updateStreamingAssistant` 的 `status === 'streaming'` 守卫兜底，这是方案 A "迟到事件不可写入"的结构保证；
- **thoughts 与停止**：停止后 thoughts 面板保留至下一轮开始（现状不变，`thoughts.length === 0 && !isStreaming` 才隐藏，ThoughtTrace.tsx:23）；
- **同毫秒 id**：`Date.now()` 同毫秒下 user/assistant 前缀不同不冲突；两条同类消息同毫秒创建在单并发前提下不可能（占位创建在 await 之前，下一轮被 `isStreaming` 阻断）；
- **实施阶段预期文件范围**（供实施任务合同确认，本阶段不修改）：`frontend/src/components/ChatPanel.tsx`、`frontend/tests/unit/ChatPanel.test.tsx`（新建）、`docs/dev/tasks/E003-*.md`（新建）、`CLAUDE.md`（§11 缺陷登记与 §17 第 8 条标记）、实施计划文档；
- **Follow-up**：历史思考轨迹折叠/持久化、regenerate、编辑历史问题、会话持久化均为后续独立任务；本任务完成后 CLAUDE.md §17 第 8 条可标记处理。
