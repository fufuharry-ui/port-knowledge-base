# E003 Message-Scoped Citations and Generation Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This repository task is configured for single-session execution; do not dispatch implementation subagents.

**Goal:** 将ChatPanel的引用、正文和生成状态迁移为assistant消息级状态，并通过ActiveRequest同步守卫解决停止生成、迟到事件和旧请求清理竞态。

**Architecture:** 保持现有后端和SSE协议不变，在ChatPanel内部使用可辨识消息联合类型、消息ID定向更新函数和ActiveRequest对象身份守卫。React消息状态负责渲染和终态一致性，ref内的stopped标志负责同步阻断停止后的缓冲事件。

**Tech Stack:** React 19、Next.js 16、TypeScript、Jest 30、Testing Library、user-event、现有streamQA异步生成器接口。

**Design spec:** `docs/superpowers/specs/2026-08-02-e003-message-scoped-citations-design.md`（已批准，Phase: DESIGN_REVISION_AWAITING_SPEC_REVIEW 之后的用户批准是本计划存在的前提）

**Base:** `origin/dev` @ `3408b41ad0eacfd99bdb1c8352581e217dbbac6e`（计划编写时 merge-base 一致，无漂移；实施第一步必须复核）

**Branch / Worktree:** `fix/e003-message-scoped-citations` @ `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e003`

## Global Constraints

- 唯一生产代码变更文件为 `frontend/src/components/ChatPanel.tsx`；
- 新建测试为 `frontend/tests/unit/ChatPanel.test.tsx`；
- 不修改后端、SSE协议、ChatBubble、ThoughtTrace、qa-stream、package.json、Jest配置或CI；
- 使用TDD：生产代码前必须先运行目标测试并确认RED；
- 不使用useReducer；
- 不抽取useChatSession；
- 不新增依赖；
- 不增加测试专用生产接口；
- 停止后保留已收到引用；
- 停止提示最多追加一次；
- 全部五类事件（thought/source/entity/delta/done）受请求身份守卫；
- completed、stopped、error均为不可逆终态；
- 旧请求catch/finally不得影响新请求；
- history仍只发送 `{role, content}`；
- thoughts仍为当前轮临时状态；
- Git和PR遵循 `docs/dev/GIT_WORKFLOW.md`；所有Git写操作仅在实施任务合同明确授权后执行；
- 后续实施不使用多Agent，单会话逐任务执行；
- `npm run build` 与 ESLint 必须分别执行，不得声称build包含lint；
- 实施在 E003 worktree 内进行，不新建worktree、不切换分支；
- 所有命令的工作目录为 `frontend/`（除Git命令外）。

## 文件地图

### Create

- `frontend/tests/unit/ChatPanel.test.tsx`
  - ChatPanel多轮引用、停止语义、迟到事件和请求身份竞态单元测试；
- `docs/dev/tasks/E003-message-scoped-citations.md`
  - RED、GREEN、验证、CI、Codex和合并事实记录。

### Modify

- `frontend/src/components/ChatPanel.tsx`
  - 消息联合类型、消息级citations/status、ActiveRequest、停止和请求身份守卫；
- `CLAUDE.md`
  - E003完成后最小更新§11和§17第8项；
- `docs/superpowers/plans/2026-08-02-e003-message-scoped-citations-implementation.md`
  - 本计划，只在计划阶段创建（实施阶段不修改）。

### Read-only

- `frontend/src/components/ChatBubble.tsx`（引用解析完全按props，无内部状态，本任务不修改）
- `frontend/src/components/ThoughtTrace.tsx`（渲染条件不变）
- `frontend/src/lib/qa-stream.ts`（事件模型与signal已满足，不修改）
- `.github/workflows/ci.yml`（不修改；`frontend-unit-build` = `npm ci` → `npm test -- --runInBand` → `npm run build`）
- `frontend/tests/unit/ChatBubble.test.tsx`（现有回归，保持绿色，不修改）
- `frontend/__mocks__/reactMarkdown.tsx`（已支持平衡括号链接解析，不修改）
- `frontend/tests/e2e/qa.spec.ts`（已冻结 skip，不修改）
- `frontend/tests/e2e/uat.spec.ts`（UAT-F06 不在CI运行，默认不修改）
- 所有后端文件。

## 已核实的代码事实（实施依据，行号对应当前 Head 89bfb7c）

- `ChatPanel.tsx:10-14` Message 仅 `{id, role, content}`；`:30` 组件级 `citations` state；`:70` 新轮 `setCitations([])`；`:99` source 事件全局覆盖；`:197` 渲染统一传全局 citations；`:35` `abortRef`；`:226-237` 停止按钮 `abortRef.current?.abort()`；`:120-135` catch/finally 无条件更新；
- `qa-stream.ts:13-18` QAEvent 五类事件；`:68-73` `streamQA(query, history, apiBase, signal)`；`:81-85` HTTP 非2xx以 delta+done 下发错误文本，不抛异常；
- `ChatBubble.tsx:25-41` `linkifyCitations` 按 props citations 解析；CitationTrigger testid `citation-trigger`；Tooltip 链接 testid `citation-link-<doc_id>`、href `/wiki/<doc_id>`；
- `ThoughtTrace.tsx:23` `thoughts.length === 0 && !isStreaming` 时隐藏；step testid `thought-step-<step>`；
- `jest.config.js:20` react-markdown 由 `__mocks__/reactMarkdown.tsx` 替代；`jest.setup.js` 已 mock next/navigation 与 next/image（next/link 不 mock，jsdom 下渲染为 `<a>`）；
- `ChatBubble.test.tsx` 为现有唯一组件级单测，只断言 trigger，不做 hover；
- E003 worktree 当前无 `frontend/node_modules`，实施 Preflight 必须 `npm ci`。

## 受控异步流测试基础设施（Task 1 建立，全文件复用）

设计要点：

- mock `@/lib/qa-stream` 模块，仅替换 `streamQA` 为 `jest.fn`；
- 每轮提问前 `queueStream()` 入队一条受控流；mock 实现按调用顺序出队，并把第4参数 `signal` 记录到 `stream.signal`，使 controller abort 可观测；
- `emit` 同步投递事件给正在等待的 `next()`（或排队）；`fail(error)` 使当前及后续 `next()` 拒绝；`close()` 使迭代正常结束；
- 不在 helper 内自动把 signal abort 转成拒绝——停止竞态测试要求"点击停止后、AbortError 到达前"由测试显式控制迟到事件注入，最后才显式 `fail(abortError())`；
- `afterEach` 释放全部已创建流的 gate，防止 Jest 悬挂。

完整 helper 代码（Task 1 写入测试文件头部）：

```typescript
/**
 * tests/unit/ChatPanel.test.tsx — ChatPanel 消息级引用与请求竞态 TDD 测试 (E003)
 * streamQA 以 jest.mock 替换为受控异步流;deferred 控制每轮推进,不依赖真实网络与真实 timer。
 */
import '@testing-library/jest-dom';
import React from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatPanel from '@/components/ChatPanel';
import type { CitationMeta, QAEvent } from '@/lib/qa-stream';
import { streamQA } from '@/lib/qa-stream';

jest.mock('@/lib/qa-stream', () => ({
    streamQA: jest.fn(),
}));

const mockedStreamQA = streamQA as jest.MockedFunction<typeof streamQA>;

/** 受控 QA 流:测试显式决定每个事件何时到达、何时失败、何时结束 */
interface ControlledQAStream {
    iterable: AsyncIterable<QAEvent>;
    emit(event: QAEvent): Promise<void>;
    fail(error: unknown): Promise<void>;
    close(): Promise<void>;
    signal?: AbortSignal;
}

function createControlledQAStream(): ControlledQAStream {
    type Pending = {
        resolve: (r: IteratorResult<QAEvent>) => void;
        reject: (e: unknown) => void;
    };
    const queue: QAEvent[] = [];
    const pending: Pending[] = [];
    let failed: { error: unknown } | null = null;
    let closed = false;

    return {
        iterable: {
            [Symbol.asyncIterator]() {
                return {
                    next(): Promise<IteratorResult<QAEvent>> {
                        if (failed) return Promise.reject(failed.error);
                        const queued = queue.shift();
                        if (queued !== undefined) {
                            return Promise.resolve({ value: queued, done: false });
                        }
                        if (closed) {
                            return Promise.resolve({ value: undefined as never, done: true });
                        }
                        return new Promise<IteratorResult<QAEvent>>((resolve, reject) => {
                            pending.push({ resolve, reject });
                        });
                    },
                };
            },
        },
        emit(event: QAEvent) {
            const p = pending.shift();
            if (p) p.resolve({ value: event, done: false });
            else queue.push(event);
            return Promise.resolve();
        },
        fail(error: unknown) {
            if (!failed) {
                failed = { error };
                while (pending.length > 0) pending.shift()!.reject(error);
            }
            return Promise.resolve();
        },
        close() {
            closed = true;
            while (pending.length > 0) {
                pending.shift()!.resolve({ value: undefined as never, done: true });
            }
            return Promise.resolve();
        },
    };
}

/** 所有已创建的流(afterEach 统一释放);waiting 队列(streamQA 调用时出队) */
let allStreams: ControlledQAStream[] = [];
let waiting: ControlledQAStream[] = [];

function queueStream(): ControlledQAStream {
    const stream = createControlledQAStream();
    allStreams.push(stream);
    waiting.push(stream);
    return stream;
}

function abortError(): Error {
    return Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
}

const DOC_A: CitationMeta[] = [
    { ref: '[1]', doc_id: 'doc_A', title: '岸桥远控技术方案', section: '第3章 网络方案' },
];
const DOC_B: CitationMeta[] = [
    { ref: '[2]', doc_id: 'doc_B', title: '5G港口网络规划', section: '第7页 表2' },
];

beforeEach(() => {
    allStreams = [];
    waiting = [];
    mockedStreamQA.mockReset();
    mockedStreamQA.mockImplementation((_query, _history, _apiBase, signal) => {
        const stream = waiting.shift();
        if (!stream) throw new Error('no controlled stream queued for this streamQA call');
        stream.signal = signal;
        // streamQA 返回类型为 AsyncGenerator;受控流仅实现 AsyncIterable,
        // 组件侧只用 for-await 消费,此处收窄以满足 ts-jest 类型检查
        return stream.iterable as unknown as AsyncGenerator<QAEvent>;
    });
});

afterEach(async () => {
    // 释放所有 generator gate,防止 Jest 悬挂
    await act(async () => {
        for (const s of allStreams) await s.close();
    });
});

async function askQuestion(user: ReturnType<typeof userEvent.setup>, text: string) {
    await user.type(screen.getByTestId('chat-input'), text);
    await user.click(screen.getByTestId('chat-submit'));
}

function assistantBubbles(): HTMLElement[] {
    return screen
        .getAllByTestId('chat-bubble')
        .filter(b => b.getAttribute('data-role') === 'assistant');
}
```

---

## Task 0: Preflight（环境与基线）

**Files:** 无变更（只读 + 安装依赖）

**Interfaces:** Consumes: `frontend/package.json`、CI `frontend-unit-build` 命令契约。Produces: 可用 node_modules、绿色基线。

- [ ] 复核 Git 基线：`git branch --show-current` 为 `fix/e003-message-scoped-citations`；`git status --short` clean；`git fetch origin --prune` 后 `git merge-base origin/dev HEAD` 仍为 `3408b41...`；若 origin/dev 已前进，停止并向用户报告，不擅自 merge；
- [ ] 确认实施任务合同已明确授权 Git 写操作（commit）；未授权时只执行到本地验证并报告；
- [ ] `cd frontend && npm ci`（worktree 无 node_modules）；
- [ ] 基线回归：`npm test -- --runInBand` —— 预期现有全部测试通过（含 ChatBubble.test.tsx），记录通过数作为基线；
- [ ] 记录基线事实到实施会话笔记（供 Task 5 任务文档引用）。

**Verify:** `npm test -- --runInBand` 退出码 0。

**Commit:** 无。

---

## Task 1: 可控流测试基础设施 + 多轮引用 RED

**Files:**
- Create: `frontend/tests/unit/ChatPanel.test.tsx`
- Read: `frontend/tests/unit/ChatBubble.test.tsx`、`frontend/__mocks__/reactMarkdown.tsx`（已定稿于上方 helper 设计）

**Interfaces:** Consumes: ChatPanel 现有 props（`onHighlight?`）与 testid 契约（`chat-input`/`chat-submit`/`chat-stop`/`chat-bubble`/`citation-trigger`/`citation-link-<doc_id>`）、`streamQA` 模块接口与 `QAEvent` 类型。Produces: `ControlledQAStream` 测试 helper、5 个多轮引用测试（对当前生产代码 RED）。

- [ ] 创建 `frontend/tests/unit/ChatPanel.test.tsx`，写入上方"受控异步流测试基础设施"完整代码；
- [ ] 在 helper 之后追加 `describe('多轮问答消息级引用', ...)`，完整测试代码如下（对应设计 §10.1 第 1、2、3、5、6 条；第 4 条停止语义属 Task 3）：

```typescript
describe('多轮问答消息级引用', () => {
    test('T1.1 第一轮回答引用文档A', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮问题');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '延迟要求≤50ms[1]。' });
        await s1.emit({ type: 'done' });

        const trigger = await screen.findByTestId('citation-trigger');
        expect(trigger).toHaveTextContent('[1]');
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T1.2 第二轮回答引用文档B,且history协议只含role/content', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '第一轮正文[1]。' });
        await s1.emit({ type: 'done' });
        await screen.findByText(/第一轮正文/);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());

        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'source', citations: DOC_B });
        await s2.emit({ type: 'delta', text: '第二轮正文[2]。' });
        await s2.emit({ type: 'done' });
        await screen.findByText(/第二轮正文/);

        const bubbles = assistantBubbles();
        expect(within(bubbles[1]).getByTestId('citation-trigger')).toHaveTextContent('[2]');
        // history 协议不变:只含 role/content,正文含第一轮文本
        expect(mockedStreamQA.mock.calls[1][1]).toEqual([
            { role: 'user', content: '第一轮' },
            { role: 'assistant', content: '第一轮正文[1]。' },
        ]);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T1.3 第二轮结束后第一条仍引用文档A(核心回归)', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '第一轮正文[1]。' });
        await s1.emit({ type: 'done' });
        await screen.findByText(/第一轮正文/);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());

        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'source', citations: DOC_B });
        await s2.emit({ type: 'delta', text: '第二轮正文[2]。' });
        await s2.emit({ type: 'done' });
        await screen.findByText(/第二轮正文/);

        const [first] = assistantBubbles();
        // 第一条仍只有 docA 的 [1] trigger,不存在 docB 的 [2] trigger
        const triggers = within(first).queryAllByTestId('citation-trigger');
        expect(triggers).toHaveLength(1);
        expect(triggers[0]).toHaveTextContent('[1]');
        expect(within(first).queryByText('[2]')).not.toBeInTheDocument();
        // 第一条的引用跳转仍指向 docA
        await user.hover(triggers[0]);
        const link = await screen.findByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(link).toHaveAttribute('href', '/wiki/doc_A');
    });

    test('T1.4 第二轮source到达前,第一轮引用不消失(setCitations([])缺陷回归)', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '第一轮正文[1]。' });
        await s1.emit({ type: 'done' });
        await screen.findByText(/第一轮正文/);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());

        queueStream(); // 第二轮流已就位,但不emit任何事件
        await askQuestion(user, '第二轮');

        // 第二轮 source 尚未到达:第一轮引用必须仍在
        const [first] = assistantBubbles();
        expect(within(first).getByTestId('citation-trigger')).toHaveTextContent('[1]');
    });

    test('T1.5 两条消息的引用跳转各自指向对应文档', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '第一轮正文[1]。' });
        await s1.emit({ type: 'done' });
        await screen.findByText(/第一轮正文/);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());

        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'source', citations: DOC_B });
        await s2.emit({ type: 'delta', text: '第二轮正文[2]。' });
        await s2.emit({ type: 'done' });
        await screen.findByText(/第二轮正文/);

        const bubbles = assistantBubbles();
        // 第二条 → doc_B
        await user.hover(within(bubbles[1]).getByTestId('citation-trigger'));
        const linkB = await screen.findByTestId('citation-link-doc_B', undefined, { timeout: 3000 });
        expect(linkB).toHaveAttribute('href', '/wiki/doc_B');
        await user.unhover(within(bubbles[1]).getByTestId('citation-trigger'));
        // 第一条 → doc_A
        await user.hover(within(bubbles[0]).getByTestId('citation-trigger'));
        const linkA = await screen.findByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(linkA).toHaveAttribute('href', '/wiki/doc_A');
    });
});
```

- [ ] **RED 运行**：`cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`；
- [ ] 确认 RED 形态并记录（预期：T1.1、T1.2 在当前代码上**通过**——单轮/末轮恰好吃到全局 citations；T1.3 **失败**——第二轮后第一条的 `[1]` 解析不到 meta、无 trigger；T1.4 **失败**——`setCitations([])` 清空全部历史引用；T1.5 **失败**——第一条 hover 不出现 `citation-link-doc_A`。若实际 RED 形态与此不符，停止并核对 helper 正确性后再继续）；
- [ ] 确认测试失败原因是生产缺陷而非测试错误（失败断言应落在 citation trigger/link 缺失，而非 helper 抛错）；
- [ ] 暂存核验：`git status --short` 仅新增测试文件；`git diff --check` 无输出。

**RED command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`（预期失败原因如上）

**GREEN command:** 本任务无（生产代码在 Task 2）。

**Regression:** `npm test -- --runInBand`（现有测试不受影响，新文件内 2 过 3 败为已知 RED 状态——仅作记录，本任务提交点允许新测试 RED，GREEN 在 Task 2 完成）。

**Commit（经实施任务合同 Git 授权后）:**
- `git add frontend/tests/unit/ChatPanel.test.tsx`
- `git commit -m "test: add ChatPanel multi-turn citation tests (RED)"`

---

## Task 2: 消息联合类型与消息级 citations/status（GREEN）

**Files:**
- Modify: `frontend/src/components/ChatPanel.tsx`

**Interfaces:** Consumes: Task 1 的 RED 测试、`CitationMeta` 类型（继续从 `@/lib/qa-stream` 导入）。Produces: 可辨识联合 `Message` 类型、`updateAssistant`/`updateStreamingAssistant` helper、消息级 citations/status 写入与渲染。

- [ ] 将 `Message` 接口（ChatPanel.tsx:10-14）替换为可辨识联合：

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

- [ ] 删除组件级 `citations` state（:30）与新轮 `setCitations([])`（:70）；
- [ ] 在 `scrollToBottom` 之后新增两个 helper（`useCallback`，完整依赖数组）：

```typescript
/** 按 id 更新 assistant 消息;非 assistant 或 id 不匹配时原样返回 */
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

- [ ] 占位消息创建（:76-80）改为 `{ id: assistantId, role: 'assistant', content: '', citations: [], status: 'streaming' }`；
- [ ] 事件循环改写：`source` → `updateStreamingAssistant(assistantId, m => ({ ...m, citations: event.citations }))`；`delta` → `updateStreamingAssistant(assistantId, m => ({ ...m, content: m.content + event.text }))`（保留 `scrollToBottom()`）；`done` → `updateStreamingAssistant(assistantId, m => ({ ...m, status: 'completed' }))` 并保留现有 `setIsStreaming(false)`（本任务为中间态，done 统一收口在 Task 3 完成）；`thought`/`entity` 不变；
- [ ] catch 保持现有 map 式更新不变（停止语义重写属 Task 3，请求身份守卫属 Task 4）；`abortRef` 与 finally 本任务不变；
- [ ] 渲染处（:197）改为 `citations={msg.role === 'assistant' ? msg.citations : []}`；
- [ ] `handleSubmit` 的 `useCallback` 依赖数组补充 `updateStreamingAssistant`；
- [ ] history 收集代码（:63-66）保持原样（联合类型下 `role`/`content` 均存在，编译与协议不变）；
- [ ] **GREEN 运行**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand` —— T1.1–T1.5 全部通过；
- [ ] 若 T1.3/T1.5 的 hover 断言在 jsdom 下不稳定（Radix Tooltip 未打开）：仅允许把 `findByTestId` 的 timeout 提高至 5000 重试一次；仍失败则停止并报告，不得修改 ChatBubble 或 mock 绕过；
- [ ] **回归**：`npm test -- --runInBand` 全部通过（含 ChatBubble.test.tsx 既有 7 项）；
- [ ] 暂存核验：`git diff --check`、`git status --short` 仅 ChatPanel.tsx。

**RED command:** 复用 Task 1 已确认的 RED（本任务开始前重跑一次确认仍 RED）。

**GREEN command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`

**Regression:** `cd frontend && npm test -- --runInBand`

**Commit:**
- `git add frontend/src/components/ChatPanel.tsx`
- `git commit -m "fix: scope citations and status per assistant message"`

---

## Task 3: ActiveRequest 同步停止守卫 + 全事件身份校验（RED → GREEN）

**Files:**
- Modify: `frontend/tests/unit/ChatPanel.test.tsx`（追加 describe）
- Modify: `frontend/src/components/ChatPanel.tsx`

**Interfaces:** Consumes: Task 2 的消息模型与 helper、`streamQA` signal 参数、停止按钮 testid `chat-stop`。Produces: `ActiveRequest` 结构、`isWritableRequest` 守卫、`handleStop` 同步终态处理、全事件请求身份校验、done 统一收口（中间态 finally）。

### 3a. RED 测试

- [ ] 追加 `describe('停止语义与迟到事件守卫', ...)`，完整测试代码：

```typescript
describe('停止语义与迟到事件守卫', () => {
    test('T3.1 收到source后停止:保留正文和引用,追加一次停止提示', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '部分正文[1]。' });
        await screen.findByText(/部分正文/);

        await user.click(screen.getByTestId('chat-stop'));
        await act(async () => { await s1.fail(abortError()); });

        const [bubble] = assistantBubbles();
        await waitFor(() => expect(bubble.textContent).toMatch(/⏹\s*\*?已停止生成\*?/));
        expect(bubble.textContent).toContain('部分正文');
        expect(within(bubble).getByTestId('citation-trigger')).toHaveTextContent('[1]');
        // 停止提示只出现一次
        expect(bubble.textContent!.match(/已停止生成/g)).toHaveLength(1);
        expect(screen.getByTestId('chat-input')).toBeEnabled();
    });

    test('T3.2 未收到source前停止:引用保持空,正文追加停止提示', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'delta', text: '部分正文' });
        await screen.findByText(/部分正文/);

        await user.click(screen.getByTestId('chat-stop'));
        await act(async () => { await s1.fail(abortError()); });

        const [bubble] = assistantBubbles();
        await waitFor(() => expect(bubble.textContent).toMatch(/已停止生成/));
        expect(within(bubble).queryByTestId('citation-trigger')).not.toBeInTheDocument();
    });

    test('T-R1 点击停止立即建立同步守卫:全部迟到事件被忽略', async () => {
        const user = userEvent.setup();
        const onHighlight = jest.fn();
        render(<ChatPanel onHighlight={onHighlight} />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '部分正文[1]。' });
        await screen.findByText(/部分正文/);

        // 点击停止;生成器尚未抛 AbortError
        await user.click(screen.getByTestId('chat-stop'));
        // 在测试控制下尝试继续产出全部五类迟到事件
        await s1.emit({ type: 'thought', step: 99, message: '迟到思考' });
        await s1.emit({ type: 'source', citations: DOC_B });
        await s1.emit({ type: 'entity', ids: ['doc_B'] });
        await s1.emit({ type: 'delta', text: '迟到正文' });
        await s1.emit({ type: 'done' });
        // 现在才让生成器抛 AbortError
        await act(async () => { await s1.fail(abortError()); });

        const [bubble] = assistantBubbles();
        // 引用仍是 A
        const triggers = within(bubble).queryAllByTestId('citation-trigger');
        expect(triggers).toHaveLength(1);
        expect(triggers[0]).toHaveTextContent('[1]');
        // 正文不追加迟到 delta
        expect(bubble.textContent).not.toContain('迟到正文');
        // onHighlight 不响应迟到 entity
        expect(onHighlight).not.toHaveBeenCalled();
        // ThoughtTrace 不增加迟到 thought
        expect(screen.queryByTestId('thought-step-99')).not.toBeInTheDocument();
        // 状态不变为 completed:停止提示存在且唯一
        expect(bubble.textContent!.match(/已停止生成/g)).toHaveLength(1);
        // UI 已恢复且不被 late done 再次改变
        expect(screen.getByTestId('chat-input')).toBeEnabled();
    });
});
```

- [ ] **RED 运行**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand`；
- [ ] 确认 RED 形态并记录（预期：T3.1、T3.2 在当前代码上**通过**——锁定现状停止行为作回归；T-R1 **失败**——点击停止后 mock 忽略 signal，迟到 source 覆盖引用、迟到 delta 追加正文、迟到 entity 调用 onHighlight、迟到 thought 出现 `thought-step-99`。多点失败均为生产缺陷证据）；
- [ ] 提交 RED 测试：
  - `git add frontend/tests/unit/ChatPanel.test.tsx`
  - `git commit -m "test: add stop guard and late-event race tests (RED)"`

### 3b. 生产实现

- [ ] 在组件内用 ActiveRequest 替代 `abortRef`（删除 :35）：

```typescript
interface ActiveRequest {
    assistantId: string;
    controller: AbortController;
    stopped: boolean;
}
```

```typescript
const activeRequestRef = useRef<ActiveRequest | null>(null);
```

- [ ] 在组件内（`activeRequestRef` 声明之后）新增请求守卫函数。它读取组件实例的 ref，仅被 `handleSubmit` 事件循环与 catch 同步调用，不进入任何 Hooks 依赖数组，因此定义为普通函数（每次渲染重建）而非 `useCallback`：

```typescript
/** 最强守卫:对象身份 + 同步停止标志(ref 内同步可读,不等待 React 状态提交) */
const isWritableRequest = (request: ActiveRequest): boolean =>
    activeRequestRef.current === request && !request.stopped;
```

- [ ] 新增 `handleStop`（`useCallback`，依赖 `updateStreamingAssistant`；顺序固定：先同步 flag、再消息终态、再解除 UI 流式、最后 abort）：

```typescript
const handleStop = useCallback(() => {
    const request = activeRequestRef.current;
    if (!request || request.stopped) return; // 重复点击幂等:停止提示最多一次
    // 1. 同步建立竞态守卫(必须先于 abort,不等待 React 状态提交)
    request.stopped = true;
    // 2. 消息终态:保留已有正文与引用,追加一次停止提示
    //    (经 updateStreamingAssistant:已完成的消息不会被改回 stopped)
    updateStreamingAssistant(request.assistantId, m => ({
        ...m,
        content: m.content + (m.content ? '\n\n' : '') + '⏹ *已停止生成*',
        status: 'stopped',
    }));
    // 3. 立即恢复输入界面
    setIsStreaming(false);
    // 4. 最后中止底层流
    request.controller.abort();
}, [updateStreamingAssistant]);
```

- [ ] `handleSubmit` 内以 ActiveRequest 替代 controller 直存：

```typescript
const controller = new AbortController();
const request: ActiveRequest = { assistantId, controller, stopped: false };
activeRequestRef.current = request;
```

- [ ] 事件循环顶部加请求身份守卫（全部五类事件统一生效）；`done` 改为只标记消息 completed 并退出循环，不再直接 `setIsStreaming(false)`：

```typescript
try {
    for await (const event of streamQA(query, history, undefined, controller.signal)) {
        // 停止或被新请求替换后,全部事件(含 thought/entity)一律忽略
        if (!isWritableRequest(request)) continue;
        switch (event.type) {
            case 'thought':
                setThoughts(prev => [...prev, {
                    step: event.step,
                    message: event.message,
                    timestamp: Date.now(),
                }]);
                break;
            case 'source':
                updateStreamingAssistant(assistantId, m => ({ ...m, citations: event.citations }));
                break;
            case 'entity':
                onHighlight?.(event.ids);
                break;
            case 'delta':
                updateStreamingAssistant(assistantId, m => ({ ...m, content: m.content + event.text }));
                scrollToBottom();
                break;
            case 'done':
                // 统一收口:只标记消息终态并退出循环;
                // isStreaming 与 ref 清理由 finally 执行(本任务为中间态,Task 4 加身份守卫)
                updateStreamingAssistant(assistantId, m => ({ ...m, status: 'completed' }));
                break;
        }
        if (event.type === 'done') break; // 记录已收到 done:退出事件循环,交给 finally 收口
    }
}
```

- [ ] catch 最小中间态改造（防止 handleStop 已追加停止提示后重复追加；完整身份守卫在 Task 4）：

```typescript
} catch (err) {
    const isAbort = err instanceof Error && err.name === 'AbortError';
    if (isAbort && request.stopped) {
        // 正常停止路径:handleStop 已完成全部终态工作,只吞掉预期中止
    } else {
        setMessages(prev => prev.map(m =>
            m.id === assistantId
                ? {
                    ...m,
                    content: isAbort
                        ? m.content + (m.content ? '\n\n' : '') + '⏹ *已停止生成*'
                        : `⚠️ 流式请求失败: ${err}`,
                }
                : m
        ));
    }
} finally {
    // 中间态:仍无条件清理;T-R2/T-R3 将暴露该缺陷,Task 4 修复
    setIsStreaming(false);
    activeRequestRef.current = null;
}
```

- [ ] 停止按钮 `onClick={() => abortRef.current?.abort()}`（:230）改为 `onClick={handleStop}`；
- [ ] `handleSubmit` 依赖数组保持 `[inputValue, isStreaming, messages, onHighlight, scrollToBottom, updateStreamingAssistant]`；不新增 eslint 禁用注释；
- [ ] **GREEN 运行**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand` —— T1.*、T3.1、T3.2、T-R1 全部通过；
- [ ] **回归**：`npm test -- --runInBand` 全部通过。

**RED command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand -t "停止语义与迟到事件守卫"`

**GREEN command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`

**Regression:** `cd frontend && npm test -- --runInBand`

**Commit:**
- `git add frontend/src/components/ChatPanel.tsx`
- `git commit -m "fix: add ActiveRequest synchronous stop guard"`

---

## Task 4: catch/finally 请求身份安全清理（RED → GREEN）

**Files:**
- Modify: `frontend/tests/unit/ChatPanel.test.tsx`（追加 describe）
- Modify: `frontend/src/components/ChatPanel.tsx`

**Interfaces:** Consumes: Task 3 的 `ActiveRequest`/`isWritableRequest`/`handleStop`。Produces: 带身份校验的 catch（AbortError 幂等、非 Abort 守卫）与 finally（对象身份条件清理）。

### 4a. RED 测试

- [ ] 追加 `describe('请求身份竞态', ...)`，完整测试代码：

```typescript
describe('请求身份竞态', () => {
    test('T-R2 旧请求的finally不得清理新请求', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        // 第一轮
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'delta', text: '第一轮正文' });
        await screen.findByText(/第一轮正文/);
        // 点击停止 → UI 应立即允许下一轮(不等 AbortError)
        await user.click(screen.getByTestId('chat-stop'));
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        // 第一轮生成器暂不结束(catch/finally 未完成),启动第二轮
        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'delta', text: '第二轮正文' });
        await screen.findByText(/第二轮正文/);
        expect(screen.getByTestId('chat-stop')).toBeInTheDocument();
        // 现在让第一轮生成器结束:旧 catch/finally 执行
        await act(async () => { await s1.fail(abortError()); });
        // 第二轮仍保持 streaming,停止按钮仍可用
        expect(screen.getByTestId('chat-stop')).toBeInTheDocument();
        expect(screen.getByTestId('chat-input')).toBeDisabled();
        expect(screen.getByText('推理中')).toBeInTheDocument();
        // 点击第二轮停止,确认停止的是第二轮 controller
        await user.click(screen.getByTestId('chat-stop'));
        expect(s2.signal?.aborted).toBe(true);
        await act(async () => { await s2.fail(abortError()); });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T-R3 旧请求的非Abort异常不得污染新请求,也不得改写已停止消息', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'delta', text: '第一轮正文' });
        await screen.findByText(/第一轮正文/);
        await user.click(screen.getByTestId('chat-stop'));
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        // 第二轮进行中
        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'delta', text: '第二轮正文' });
        await screen.findByText(/第二轮正文/);
        // 第一轮随后抛非 Abort 错误
        await act(async () => { await s1.fail(new Error('network boom')); });
        // 第二轮不受影响
        expect(screen.getByText('推理中')).toBeInTheDocument();
        expect(screen.getByTestId('chat-stop')).toBeInTheDocument();
        const bubbles = assistantBubbles();
        expect(bubbles[1].textContent).toContain('第二轮正文');
        expect(bubbles[1].textContent).not.toContain('⚠️ 流式请求失败');
        // 已停止的第一轮也不被旧异常改写
        expect(bubbles[0].textContent).toContain('已停止生成');
        expect(bubbles[0].textContent).not.toContain('⚠️ 流式请求失败');
        // 收尾:正常停止第二轮
        await user.click(screen.getByTestId('chat-stop'));
        await act(async () => { await s2.fail(abortError()); });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T-R4 正常done统一收口:输入恢复,下一轮可启动,无双重清理', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'delta', text: '正文' });
        await s1.emit({ type: 'done' });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        expect(screen.queryByText('推理中')).not.toBeInTheDocument();
        // 下一轮可启动并正常 streaming
        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'delta', text: '第二轮正文' });
        await screen.findByText(/第二轮正文/);
        expect(screen.getByTestId('chat-stop')).toBeInTheDocument();
        expect(screen.getByText('推理中')).toBeInTheDocument();
        await s2.emit({ type: 'done' });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T4.5 当前请求的非Abort异常:整体替换为错误文本(现状产品行为)', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '部分正文[1]。' });
        await screen.findByText(/部分正文/);
        await act(async () => { await s1.fail(new Error('boom')); });
        const [bubble] = assistantBubbles();
        await waitFor(() => expect(bubble.textContent).toMatch(/⚠️ 流式请求失败/));
        expect(bubble.textContent).not.toContain('部分正文'); // 整体替换,不追加
        expect(screen.getByTestId('chat-input')).toBeEnabled();
    });
});
```

- [ ] **RED 运行**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand`；
- [ ] 确认 RED 形态并记录（预期在 Task 3 中间态代码上：T-R2 **失败**——旧 finally 无条件 `setIsStreaming(false)` + 清空 ref，第二轮的"推理中"与停止按钮消失；T-R3 **失败**——旧 catch 的 map 式更新把已停止的第一轮正文改写为 `⚠️ 流式请求失败`；T-R4 **通过**——正常路径回归锁定；T4.5 **通过**——现状错误行为锁定）；
- [ ] 提交 RED 测试：
  - `git add frontend/tests/unit/ChatPanel.test.tsx`
  - `git commit -m "test: add request identity race tests (RED)"`

### 4b. 生产实现

- [ ] 将 catch/finally 重写为最终形态（替换 Task 3 中间态）：

```typescript
} catch (err) {
    const isAbort = err instanceof Error && err.name === 'AbortError';
    if (isAbort) {
        if (!request.stopped) {
            // 异常路径(如 reader 自发中止):执行一次幂等兜底停止处理
            request.stopped = true;
            updateStreamingAssistant(assistantId, m => ({
                ...m,
                content: m.content + (m.content ? '\n\n' : '') + '⏹ *已停止生成*',
                status: 'stopped',
            }));
            if (activeRequestRef.current === request) setIsStreaming(false);
        }
        // 正常停止路径:handleStop 已完成全部终态工作,只吞掉预期中止
    } else if (activeRequestRef.current === request && !request.stopped) {
        // 仅当前未停止请求的失败允许标记 error
        // (整体替换正文为错误文本的现状产品行为不变;保留已收到 citations)
        updateStreamingAssistant(assistantId, m => ({
            ...m,
            content: `⚠️ 流式请求失败: ${err}`,
            status: 'error',
        }));
    }
    // 旧请求异常:忽略,不得影响新请求的消息、isStreaming 或控制器
} finally {
    // 身份安全清理:只清理自己这一轮的请求状态
    if (activeRequestRef.current === request) {
        activeRequestRef.current = null;
        setIsStreaming(false);
    }
}
```

- [ ] **GREEN 运行**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand` —— 全部通过；
- [ ] **回归**：`npm test -- --runInBand` 全部通过。

**RED command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand -t "请求身份竞态"`

**GREEN command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`

**Regression:** `cd frontend && npm test -- --runInBand`

**Commit:**
- `git add frontend/src/components/ChatPanel.tsx`
- `git commit -m "fix: guard catch/finally by request identity"`

---

## Task 5: 全量验证 + 文档收口

**Files:**
- Modify: `CLAUDE.md`（§11 末尾一行、§17 第 8 项）
- Create: `docs/dev/tasks/E003-message-scoped-citations.md`
- Read: `.github/workflows/ci.yml`、`docs/dev/GIT_WORKFLOW.md`

**Interfaces:** Consumes: Task 1–4 全部 GREEN 事实、CI required checks 契约。Produces: 完整验证证据、任务文档、CLAUDE.md 最小更新。

- [ ] **完整 Jest**：`cd frontend && npm test -- --runInBand` —— 全部通过，记录通过/失败/跳过数量；
- [ ] **ESLint（独立于 build，针对触及文件）**：`cd frontend && npx eslint src/components/ChatPanel.tsx tests/unit/ChatPanel.test.tsx` —— 退出码 0；不得用 `npm run build` 替代 lint，不得声称 build 包含 lint；
- [ ] **生产构建**：`cd frontend && npm run build` —— Next 16 生产构建（含类型检查）通过；
- [ ] **范围核验**：`git status --short`、`git diff --check`、`git diff origin/dev..HEAD --name-status` —— 生产代码仅 ChatPanel.tsx，测试仅 ChatPanel.test.tsx，另有 CLAUDE.md、任务文档、设计规格与本计划；无其他变更；
- [ ] **后端不受影响声明确认**：本任务未触碰任何 Python 文件；本地不重跑后端 pytest，但须在任务文档说明 CI `python-core` 预期不受影响的理由（后端零变更）；
- [ ] 创建 `docs/dev/tasks/E003-message-scoped-citations.md`，内容至少包含：任务身份（E003、分支、worktree、base）、问题描述与根因（引用生命周期错置于组件级状态 + 停止/清理竞态）、设计规格与本计划链接、各任务 RED/GREEN 证据（命令、退出码、通过/失败数）、T1/T3/T-R 测试与设计 §10 验收的映射、完整验证证据（Jest/ESLint/build 的命令与结果）、范围核验输出、剩余风险（Radix Tooltip jsdom hover 依赖 200ms 真实延迟经 waitFor 轮询消化；E2E 与 Live UAT 不在本任务范围）、Git 操作记录；
- [ ] CLAUDE.md 最小更新（与 E001/E002/T001 的划线+引用体例一致）：
  - §11 末尾"当前 `frontend/src/components/ChatPanel.tsx` 可能存在全局citations覆盖历史回答的已知缺陷，尚未修复。"改为划线形式并追加"（E003已处理：引用、正文和生成状态迁移为assistant消息级状态，ActiveRequest请求身份守卫覆盖停止竞态与旧请求清理；详见 `docs/dev/tasks/E003-message-scoped-citations.md`）"；
  - §17 第 8 项"多轮问答引用可能使用全局状态；"改为划线形式并追加同样的 E003 已处理说明；
- [ ] 最终回归：`npm test -- --runInBand` 再次确认绿色（文档变更不影响，但声明必须基于新鲜命令）。

**Verify commands（全部需在实施会话新鲜运行并记录退出码）:**

```bash
cd frontend && npm test -- --runInBand
cd frontend && npx eslint src/components/ChatPanel.tsx tests/unit/ChatPanel.test.tsx
cd frontend && npm run build
git status --short && git diff --check && git diff origin/dev..HEAD --name-status
```

**Commit:**
- `git add CLAUDE.md docs/dev/tasks/E003-message-scoped-citations.md`
- `git commit -m "docs: record E003 message-scoped citations completion"`

---

## 完成与移交（不属于任何 Task 的自动动作）

实施会话完成 Task 5 后必须停止并报告，等待用户决定。未经用户明确授权不得执行：`git push`、`gh pr create`、PR 评论 `@codex review`、merge。

用户授权后的标准后续（按 `docs/dev/GIT_WORKFLOW.md`）：push 分支 → 创建 PR（base=`dev`）→ 三个 required checks（repository-integrity、python-core、frontend-unit-build）全绿 → PR 评论 `@codex review` → 验证并处理 Codex 意见（先验证再修复或技术性回复）→ Head 变化后重跑 checks 并对新 Head 重新触发 Codex → 所有 thread 解决 → 使用 `--match-head-commit` 守护的 merge commit 合并。

## 验收映射（设计 §11 → 本计划）

| 设计验收 | 覆盖 |
|---|---|
| §11.2 CLAUDE.md §11 六条多轮验收 | T1.1–T1.5、T3.1 |
| §11.3 停止后无需等待 AbortError 即可阻止迟到事件 | T-R1 |
| §11.4 late thought/entity 也被忽略 | T-R1 |
| §11.5 停止提示只追加一次 | T3.1、T-R1 |
| §11.6 第一轮 finally 不清理第二轮请求 | T-R2 |
| §11.7 第一轮 catch 不改变第二轮 UI/消息 | T-R3 |
| §11.8 第二轮停止按钮在第一轮 finally 后可用 | T-R2 |
| §11.9 done/stop/error 三路径按请求身份收口 | T-R4、T3.1、T4.5 |
| §11.10 不再使用独立 abortRef | Task 3 实现（grep `abortRef` 在 ChatPanel.tsx 无残留） |
| §11.11 双层守卫 | T-R1（请求层）+ T3.1/T-R3（消息 status 终态层） |
| §11.12/13 Jest 全绿 + build | Task 5 |
| §11.14 生产代码仅 ChatPanel.tsx | Task 5 范围核验 |
| §11.15 CI frontend-unit-build 绿 | PR 阶段（用户授权后） |

## 实施风险与应对

- **Radix Tooltip jsdom hover**：Tooltip.Provider `delayDuration=200` 为真实延迟，由 `findByTestId(..., { timeout: 3000 })` 轮询消化，不使用 fake timers；若仍不稳定，仅允许提高 timeout 至 5000 重试一次，再失败则停止报告（不得修改 ChatBubble/mock/生产代码绕过）；
- **act 警告**：所有 `fail()` 调用包在 `act` 中；`emit` 后接 `findBy`/`waitFor` 自动包裹；出现警告时先调整测试时序，不得屏蔽警告；
- **Jest 悬挂**：`afterEach` 统一 `close()` 全部已创建流；`--runInBand` 运行；若出现悬挂，检查是否有未释放的 pending `next()`；
- **同毫秒 id**：`user-${Date.now()}`/`assistant-${Date.now()}` 前缀不同不冲突；单并发前提下两条 assistant 同毫秒不可能（占位创建在 await 前，下一轮被 isStreaming 阻断）；
- **中间态一致性**：Task 2/3 提交点的中间态均保持既有测试绿色；Task 3 的无条件 finally 是刻意的中间态，由 T-R2/T-R3 暴露并在 Task 4 修复——不得跳过 Task 4 直接交付 Task 3 状态；
- **Base 漂移**：Task 0 复核 merge-base；实施期间 origin/dev 前进时停止报告，不擅自 merge。
