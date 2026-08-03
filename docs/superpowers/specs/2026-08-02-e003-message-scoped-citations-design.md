# E003 Message-Scoped Citations and Generation Status Design

> Date: 2026-08-02 | Task: E003 | Phase: DESIGN_REVISION_AWAITING_SPEC_REVIEW

## 1. Status and Context

- Task: E003
- Phase: **DESIGN_REVISION_AWAITING_SPEC_REVIEW**（产品选择与实现方案已获用户批准；规格经用户审查发现两个竞态阻断问题，本次修订增加 ActiveRequest 同步守卫与请求身份安全清理；修订后规格等待用户再次审阅，未开始实施）
- Base: `origin/dev` @ `3408b41ad0eacfd99bdb1c8352581e217dbbac6e`（与任务预期一致，无漂移；即 T001 合并提交）
- Branch: `fix/e003-message-scoped-citations`
- Worktree: `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e003`
- 风险类型： frontend-state / streaming-state
- 生产缺陷现状： 多轮问答中历史 assistant 消息的引用被最新一轮的全局 citations 覆盖（CLAUDE.md §11 与 §17 第 8 条已登记的已知缺陷，未修复）；
- 规格审查指出的两个阻断问题（本次修订的核心目标）：
  1. 点击停止后、AbortError 进入 catch 前，迟到事件仍可能写入（原设计仅靠消息 `status` 守卫，而 React `setMessages` 是异步的，停止到状态提交之间存在窗口）；
  2. 旧请求的 `finally` 可能清除新请求的控制器或 `isStreaming` 状态（原设计 `finally` 无条件清空 `abortRef` 并 `setIsStreaming(false)`，无法区分新旧请求）。
- 已批准的产品选择（不得重新打开，除非代码事实证明不可行）：
  1. 每条 assistant 消息独立保存 `content`、`citations`、`status`；思考轨迹仍是当前轮临时执行状态，不保存到历史消息；
  2. 停止生成采用方案 A：已收到 source 后停止 → 保留已有正文和引用；未收到 source 前停止 → 引用保持空数组；正文追加"⏹ 已停止生成"；status 变为 `stopped`；停止后迟到的 source、delta、done 不得继续修改消息；
  3. 实现采用方案 1：直接在 ChatPanel 内改为消息级状态；可辨识联合类型；小型按 assistantId 更新消息的辅助函数；不使用 useReducer；不抽取 useChatSession；不修改后端；不修改 SSE 协议；原则上不修改 ChatBubble 和 qa-stream；
  4. 用户批准根据规格审查意见增加 ActiveRequest 同步守卫和请求身份安全清理；该批准不等于实施批准，修订后仍需再次完成规格审阅。

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

设计层面另有两个必须同步解决的竞态（规格审查指出，详见 §7.4、§7.6、§7.9）：

- **停止竞态**：停止信号与 AbortError 进入 catch 之间，已缓冲的事件仍可能写入消息；修复消息级状态时必须一并建立同步守卫，否则"停止后迟到事件不可写入"（方案 A 最后一条）无法兑现；
- **请求身份竞态**：停止后 UI 立即恢复输入，用户可马上发起下一轮；若旧请求的 `finally` 无条件清理全局控制器引用和 `isStreaming`，会清除新请求的控制器或流式状态，使新请求的停止按钮失效或输入状态错乱。

## 3. Goals

- 每条 assistant 消息独立拥有正文、引用和生成状态（message-scoped）；
- 历史消息不受后续轮次 source/delta/done/error 影响；
- 停止后保留已收到的引用与正文（方案 A）；
- 点击停止后**立即同步**阻断所有后续事件写入，无需等待 AbortError 进入 catch；
- 停止后迟到的 thought/source/entity/delta/done 全部不可继续写入；
- 旧请求的 catch/finally 不得影响新请求的控制器、消息或 `isStreaming` 状态；
- 多轮 history 协议保持 `{role, content}`（后端契约不变）；
- 当前轮 ThoughtTrace 行为保持（仅最新 assistant 消息前展示、流式结束隐藏逻辑不变）；
- 单轮问答与引用 Tooltip 无回归（ChatBubble 行为不变）；
- CLAUDE.md §11 的六条多轮问答验收全部可验证。

## 4. Non-goals

- 历史思考轨迹持久化；
- 思考轨迹折叠 UI；
- 重新生成（regenerate）；
- 编辑历史问题；
- 多请求并发（`isStreaming` 期间禁止发送的现状不变；ActiveRequest 不是并发请求框架）；
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

- 停止按钮仅在 `isStreaming` 时渲染（ChatPanel.tsx:226-237），当前由 `abortRef.current?.abort()` 触发——本设计将其替换为 ActiveRequest 的同步停止处理（§7.6）；
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
| `abortRef` | `useRef<AbortController>` | **删除**，由 `activeRequestRef`（§7.2）替代，控制器经 `activeRequestRef.current.controller` 访问 |

### 7.2 ActiveRequest 与请求身份守卫

以消息身份明确的活动请求结构替代单独的 `abortRef`：

```typescript
interface ActiveRequest {
    assistantId: string;   // 目标 assistant 消息 id
    controller: AbortController;
    stopped: boolean;      // 同步竞态守卫
}

const activeRequestRef = useRef<ActiveRequest | null>(null);

/** 最强守卫：对象身份 + 同步停止标志 */
const isWritableRequest = (request: ActiveRequest): boolean =>
    activeRequestRef.current === request && !request.stopped;
```

说明：

- `assistantId` 用于定位目标消息；
- 对象身份（`activeRequestRef.current === request`）用于区分旧请求和新请求：新请求创建并写入 ref 后，旧 request 的所有事件、catch、finally 自动失效；
- `stopped` 是 ref 内的同步标志，在点击停止的同一同步执行栈内即可阻断缓冲事件，不等待 React 状态提交，也不等待 AbortError；
- 这不是并发请求框架：UI 仍只允许一个活动请求（`isStreaming` 单并发前提不变）；
- 独立的 `abortRef` 不再需要，由 `activeRequestRef` 统一承担。

### 7.3 消息 status 状态机（每条 assistant 消息）

```text
创建占位 → streaming
streaming --done事件------> completed
streaming --用户停止------> stopped
streaming --非Abort异常---> error
completed / stopped / error 均为终态
```

终态不可逆：

- `completed` 不能变成 `stopped`；
- `stopped` 不能变成 `completed`；
- `error` 不能被迟到 `done` 覆盖；
- 终态后的任何 source/delta/done/error 写入一律忽略。

### 7.4 双层写入守卫

所有流式事件写入必须**同时**通过两层守卫：

1. **请求层（同步）**：`isWritableRequest(request)`，即 `activeRequestRef.current === request && !request.stopped`——同步阻断旧请求、已停止请求和被替换请求；
2. **消息层（状态机）**：目标 assistant 消息 `status === 'streaming'`——保护消息状态机终态不可逆。

职责划分：

- React 的 `setMessages` 是异步的，点击停止到消息状态提交之间存在窗口，单靠消息 `status` 无法覆盖该窗口；`request.stopped` 与对象身份是同步可读的，承担停止竞态的第一道防线；
- 反过来，消息 `status` 守卫保证即使请求层出现意外路径（如兜底停止处理之外的异常时序），终态消息也不会被改写；
- 两层缺一不可。本设计明确：**不**声称单靠 `updateStreamingAssistant` 即可完全解决迟到事件竞态。

### 7.5 事件处理矩阵（handleSubmit 的 for-await 循环内）

**全部五类事件**在处理前都执行请求身份与 `stopped` 检查（`isWritableRequest(request)`），不通过则忽略该事件——不仅是 source、delta 和 done：

| 事件 | 通过请求守卫后的处理 |
|---|---|
| `thought` | `setThoughts` 追加（组件级，不变） |
| `source` | `updateStreamingAssistant(assistantId, m => ({...m, citations: event.citations}))` —— 写入该消息的 citations |
| `entity` | `onHighlight?.(event.ids)`（不变） |
| `delta` | `updateStreamingAssistant(assistantId, m => ({...m, content: m.content + event.text}))` |
| `done` | 见 §7.7 统一收口 |

停止后（`request.stopped === true`）：

- late `thought` 不得继续增加 ThoughtTrace；
- late `entity` 不得继续触发 `onHighlight`；
- late `source` 不得替换引用；
- late `delta` 不得追加正文；
- late `done` 不得把 `stopped` 改成 `completed`。

旧请求被新请求替换后（`activeRequestRef.current !== request`），其所有事件同样全部忽略。

### 7.6 停止按钮的同步终态建立（handleStop）

停止处理**不得**只调用 `controller.abort()` 再由 catch 标记 stopped。必须按以下固定顺序执行：

1. 读取 `const request = activeRequestRef.current`；
2. 无活动请求或 `request.stopped` 已为 `true` → 直接返回（保证停止提示最多追加一次，重复点击幂等）；
3. **同步**设置 `request.stopped = true`；
4. 通过 `updateStreamingAssistant(request.assistantId, ...)` 把目标消息更新为 `status: 'stopped'`：保留已有 content，保留已有 citations，追加一次 `⏹ *已停止生成*`（保持现有 markdown 斜体文本与空行分隔，避免视觉回归；经 `updateStreamingAssistant` 应用，故若消息已因 done 进入 `completed`，停止点击不会把 `completed` 改回 `stopped`）；
5. 立即 `setIsStreaming(false)`，恢复输入界面；
6. 最后调用 `request.controller.abort()`。

关键原则：`request.stopped = true` 必须发生在 `controller.abort()` 之前。原因：React 消息状态更新是异步的；`request.stopped` 作为 ref 内同步标志，可立即阻止缓冲事件；单靠消息的 `status === 'streaming'` 不足以覆盖停止到 catch 之间的窗口。

### 7.7 done 的统一收口

`done` 事件处理：

- 先校验 `isWritableRequest(request)`，不通过则忽略（late done 不得把 `stopped` 改成 `completed`）；
- 通过 `updateStreamingAssistant` 将目标消息 `status` 改为 `completed`；
- 记录已收到 `done`（for-await 循环内的局部标志，使循环正常退出，并使随后的 catch/finally 能区分"正常完成"与"异常中断"）；
- 退出当前事件循环；
- **不**在此处清空 `activeRequestRef`，**不**在此处修改 `isStreaming`——最终清理由带身份校验的 `finally` 统一执行（§7.9），避免 done 路径误清属于其他请求的全局状态。

### 7.8 catch 的身份安全错误处理

**AbortError**：正常点击停止时，handleStop 已同步完成全部终态工作（`request.stopped`、消息 `status: 'stopped'`、追加停止提示、`setIsStreaming(false)`）。因此 AbortError 的 catch 通常只负责吞掉预期中止，**不得再次追加停止提示**。仅在"AbortError 但 `request.stopped` 仍为 `false`"的异常路径（如 reader 自发中止）才允许执行一次幂等兜底停止处理（与 handleStop 第 3–5 步等价的幂等版本：先置 `stopped`，再经 `updateStreamingAssistant` 标记消息，再解除 UI 流式状态）。

**非 Abort 异常**：仅当 `isWritableRequest(request)` 成立（即 `activeRequestRef.current === request && !request.stopped`）时才允许：

- 将对应 assistant 消息设为 `error`——保持现有"整体替换正文为 `` `⚠️ 流式请求失败: ${err}` ``"的用户可观察行为（Non-goal：错误提示视觉重构）；
- 保留已收到的 citations。

旧请求的异常不得影响新请求的消息、`isStreaming` 或控制器，只能被忽略。

### 7.9 finally 的身份安全清理

`handleSubmit` 内在发起请求时创建局部请求对象并发布：

```typescript
const request: ActiveRequest = {
    assistantId,
    controller,
    stopped: false,
};
activeRequestRef.current = request;
```

`finally` 必须使用创建请求时捕获的局部 `request` 对象做身份校验：

```typescript
finally {
    if (activeRequestRef.current === request) {
        activeRequestRef.current = null;
        setIsStreaming(false);
    }
}
```

禁止无条件执行 `activeRequestRef.current = null; setIsStreaming(false);`。

必须明确：

- 旧请求 `finally` 发现 `current` 已经是新请求时，不得清理；
- 旧请求 `finally` 不得让新请求的停止按钮失效（停止按钮读取的是 `activeRequestRef.current.controller`）；
- 旧请求 `finally` 不得把新请求的 `isStreaming` 设为 `false`。

### 7.10 辅助函数与 Hooks 依赖

```typescript
/** 按 id 更新 assistant 消息；非 assistant 或 id 不匹配时原样返回 */
const updateAssistant = useCallback((
    id: string,
    updater: (m: AssistantMessage) => AssistantMessage,
) => {
    setMessages(prev => prev.map(m =>
        m.id === id && m.role === 'assistant' ? updater(m) : m,
    ));
}, []);

/** 仅当消息仍处于 streaming 时才应用更新——终态消息的迟到写入一律丢弃 */
const updateStreamingAssistant = useCallback((
    id: string,
    updater: (m: AssistantMessage) => AssistantMessage,
) => {
    updateAssistant(id, m => (m.status === 'streaming' ? updater(m) : m));
}, [updateAssistant]);
```

Hooks 稳定性要求：

- `updateAssistant` 使用 `useCallback`；
- `updateStreamingAssistant` 使用 `useCallback` 并依赖 `updateAssistant`；
- `handleStop` 使用 `useCallback`；
- `handleSubmit` 正确声明这些 helper 依赖；
- 不通过禁用 eslint 规则隐藏依赖；
- 所有消息更新仍使用函数式 `setMessages`。

### 7.11 渲染变更

- ChatBubble 调用改为消息级：`citations={msg.role === 'assistant' ? msg.citations : []}`（替换 ChatPanel.tsx:197 的全局 citations）；
- ThoughtTrace 渲染条件不变（仅最后一条 assistant 消息前、组件级 thoughts）；
- `setCitations([])`（ChatPanel.tsx:70）与组件级 `citations` state 删除；新轮的"清空"通过创建 `citations: []` 的新消息自然达成，历史消息引用因此不再被清空；
- 占位消息创建为 `{ id: assistantId, role: 'assistant', content: '', citations: [], status: 'streaming' }`。

## 8. 状态流程与事件转换表

### 8.1 消息状态流程

```text
创建 → streaming
正常 done → completed
用户停止 → stopped
非 Abort 异常 → error
completed / stopped / error 均为终态
```

### 8.2 活动请求生命周期

正常路径：

```text
创建 request
→ activeRequestRef.current = request
→ 正常事件写入
→ done / catch
→ finally 按对象身份清理
```

用户停止：

```text
request.stopped = true
→ 消息 stopped
→ UI 解除 streaming
→ controller.abort()
→ catch 不得重复写入
→ finally 仅清理同一 request
```

停止后立即开始下一轮：

```text
第一轮 request1 停止
→ isStreaming = false
→ 第二轮创建 request2 并写入 activeRequestRef
→ request1 后续事件因对象身份不匹配被忽略
→ request1 finally 不得清理 request2
```

### 8.3 事件转换表

| 请求身份 | 消息状态 | 事件 | 处理 |
|---|---|---|---|
| 当前且未停止 | streaming | thought | 追加当前 thoughts |
| 当前且未停止 | streaming | source | 写入本消息 citations |
| 当前且未停止 | streaming | entity | 调用 onHighlight |
| 当前且未停止 | streaming | delta | 追加本消息 content |
| 当前且未停止 | streaming | done | 消息 → completed |
| 当前 | streaming | stop click | 同步 stopped、保留引用、abort |
| 当前且已停止 | stopped | late thought | 忽略 |
| 当前且已停止 | stopped | late source | 忽略 |
| 当前且已停止 | stopped | late entity | 忽略 |
| 当前且已停止 | stopped | late delta | 忽略 |
| 当前且已停止 | stopped | late done | 忽略 |
| 非当前旧请求 | 任意 | 任意事件 | 忽略 |
| 当前且未停止 | streaming | non-abort error | 消息 → error |
| 非当前旧请求 | 任意 | catch/finally | 不修改当前 UI 或 ref |

不变量：

- 停止提示最多追加一次（handleStop 第 2 步早退 + catch 不重复追加）；
- `completed` 不能变成 `stopped`；
- `stopped` 不能变成 `completed`；
- `error` 不能被迟到 `done` 覆盖。

## 9. Implementation Organization（已批准方案 1 的落位）

- 唯一生产代码变更文件：`frontend/src/components/ChatPanel.tsx`；
- 不修改：`qa-stream.ts`（事件模型与 signal 已满足）、`ChatBubble.tsx`（已按 props 独立解析）、`ThoughtTrace.tsx`、`page.tsx`、后端、SSE 协议；
- 不使用 useReducer、不抽取 useChatSession；
- 独立 `abortRef` 删除，由 `activeRequestRef` 替代（§7.2）；
- `CitationMeta` 类型继续从 `@/lib/qa-stream` 导入；
- 消息 id 生成保持 `user-${Date.now()}` / `assistant-${Date.now()}` 前缀形式（前缀不同，同毫秒不冲突；id 生成器重构不是本任务目标）。

## 10. Test Strategy（实施阶段执行，本阶段不创建）

新增 `frontend/tests/unit/ChatPanel.test.tsx`（Jest + @testing-library/react，`jest.mock('@/lib/qa-stream')` 提供可控 async generator）。

### 10.1 多轮引用验收（CLAUDE.md §11 六条）

1. 第一轮回答引用文档 A（source: docA → 该消息渲染 docA 的 citation-trigger）；
2. 第二轮回答引用文档 B（source: docB → 第二条消息渲染 docB）；
3. 第二轮结束后**第一条仍引用文档 A**（核心回归：docA 的 `citation-trigger`/`citation-link-doc_A` 仍存在且指向 docA，第一条中不存在 docB 链接）；
4. 停止生成不破坏已接收内容（source 后停止：正文保留、docA 引用保留、追加停止标记、status=stopped）；
5. 新请求不覆盖旧消息状态（第二轮 source 到达前，第一轮引用不消失——现状的 `setCitations([])` 缺陷回归守卫）；
6. 引用跳转仍指向对应文档（`citation-link-<doc_id>` 的 href 为 `/wiki/<doc_id>`，逐消息独立）。

另需覆盖：未收到 source 前停止 → 引用为空数组；非 abort 异常 → status=error 且错误文本行为不变；单轮问答与引用 Tooltip 回归（现有 ChatBubble.test.tsx 不修改、保持绿色）。

### 10.2 竞态测试合同（本次修订新增）

**T-R1 点击停止立即建立同步守卫**。构造受控异步生成器：先 yield source A；yield 部分 delta；暂停在 deferred gate；用户点击停止；**尚未让生成器抛 AbortError**；在测试控制下尝试继续 yield：thought、source B、entity、delta、done。断言：

- 停止后引用仍是 A；
- 正文不追加迟到 delta；
- `onHighlight` 不响应迟到 entity；
- ThoughtTrace 不增加迟到 thought；
- 状态不变为 completed；
- 停止提示只出现一次。

**T-R2 旧 finally 不得清理新请求**（阻止"旧 finally 清空新 request"的核心回归守卫）。步骤：第一轮开始；点击停止，使 UI 允许下一轮；第一轮生成器暂不结束，catch/finally 尚未完成；启动第二轮；第二轮处于 streaming，停止按钮可见；再让第一轮生成器结束并执行旧 finally。断言：

- 第二轮仍保持 streaming；
- 第二轮停止按钮仍可用；
- 点击第二轮停止，确认停止的是第二轮 controller。

**T-R3 旧请求异常不得污染新请求**。步骤：第一轮停止；第二轮开始；第一轮随后抛出非 Abort 错误。断言：第二轮正文、引用、`isStreaming` 和停止控制器均不受影响。

**T-R4 正常 done 统一收口**。断言：

- done 把目标消息设为 completed；
- 当前请求最终被清理（activeRequestRef 归零、输入恢复）；
- 下一轮可以启动；
- 不存在双重清理或旧 finally 影响下一轮。

**T-R5 测试工具约束**：

- mock `streamQA`；
- 使用 deferred Promise 控制每轮推进；
- 不依赖真实网络；
- 不使用不稳定的真实 timer 等待；
- 使用 `act`、`waitFor` 和 `userEvent`；
- 每个测试结束必须释放所有 generator gate，防止 Jest 悬挂；
- controller abort 应可观测（mock 的 AbortController 或 spy）；
- 不为了测试向生产组件增加测试专用接口。

E2E 不在 CI 门禁内；`uat.spec.ts` F06 不修改（实施阶段如 mock 契约需要补充，另行评估，默认不动）。

## 11. Acceptance Criteria

1. §3 Goals 全部满足；
2. CLAUDE.md §11 六条多轮问答验收全部由自动化测试证明；
3. 点击停止后，无需等待 AbortError 即可阻止所有迟到事件（T-R1）；
4. 停止后的 late thought 和 late entity 也被忽略（T-R1）；
5. 停止消息只追加一次停止提示（T-R1）；
6. 第一轮 finally 不得清理第二轮活动请求（T-R2）；
7. 第一轮 catch 不得改变第二轮 UI 或消息（T-R3）；
8. 第二轮停止按钮在第一轮 finally 后仍可用（T-R2）；
9. 正常 done、stop、error 三种结束路径均按请求身份收口（T-R4 及异常路径测试）；
10. 不再使用独立 `abortRef`；
11. 所有异步写入均同时受 request 身份和消息 status 保护（§7.4）；
12. 现有 Jest 全部测试保持绿色（`npm test -- --runInBand`）；
13. `npm run build` 通过（Next 16 生产构建，含类型检查与 touched-file lint）；
14. 生产代码变更仅限 `ChatPanel.tsx`；后端、SSE 协议、ChatBubble、qa-stream 零变更；
15. CI `frontend-unit-build` 绿色；
16. 完整后端 pytest 绿色不受本任务影响（后端零变更）。

## 12. Risks and Follow-up

- **React 消息状态非同步**：`setMessages` 不立即提交，停止点击到状态生效之间存在窗口。缓解：同步竞态由 `request.stopped` 和对象身份处理（ref 内同步可读）；消息 `status` 负责渲染与终态一致性；不依赖 `setMessages` 立即完成；
- **停止与缓冲事件**：abort 后 for-await 退出前可能仍有已缓冲事件进入循环体。缓解：stop click 先设置同步 flag（§7.6 顺序保证 `stopped = true` 在 `abort()` 之前）；全事件身份检查（§7.5）；消息 status 终态守卫（§7.4）；
- **旧 finally 与新请求**：停止后 UI 立即恢复，新一轮可能在旧请求 finally 执行前启动。缓解：每轮局部 `request` 对象；`activeRequestRef` 对象身份比较；finally 条件清理（§7.9）；
- **旧 catch 与新请求**：旧请求的迟到异常可能在新请求进行中到达。缓解：catch 在写消息或 UI 前校验 request 身份（`isWritableRequest`）；旧错误只能被忽略，不能覆盖新请求（§7.8）；
- **helper 依赖**：`useCallback` 依赖遗漏会导致闭包内 helper 陈旧。缓解：`useCallback` + 完整依赖数组（§7.10）；touched-file lint 验证，不通过禁用 eslint 规则隐藏依赖；
- **React state 闭包**：`handleSubmit` 依赖 `messages` 收集 history（现状不变）；事件写入全部经 `setMessages` 函数式更新，不读取闭包内 `messages`，无陈旧状态风险；
- **thoughts 与停止**：停止后 thoughts 面板保留至下一轮开始（现状不变，`thoughts.length === 0 && !isStreaming` 才隐藏，ThoughtTrace.tsx:23）；旧请求的 late thought 由请求身份守卫阻断，不会混入新一轮 thoughts；
- **同毫秒 id**：`Date.now()` 同毫秒下 user/assistant 前缀不同不冲突；两条同类消息同毫秒创建在单并发前提下不可能（占位创建在 await 之前，下一轮被 `isStreaming` 阻断）；
- **实施阶段预期文件范围**（供实施任务合同确认，本阶段不修改）：`frontend/src/components/ChatPanel.tsx`、`frontend/tests/unit/ChatPanel.test.tsx`（新建）、`docs/dev/tasks/E003-*.md`（新建）、`CLAUDE.md`（§11 缺陷登记与 §17 第 8 条标记）、实施计划文档；
- **Follow-up**：历史思考轨迹折叠/持久化、regenerate、编辑历史问题、会话持久化均为后续独立任务；本任务完成后 CLAUDE.md §17 第 8 条可标记处理。
