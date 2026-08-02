# E003 Message-Scoped Citations and Generation Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This repository task is configured for single-session execution; do not dispatch implementation subagents.

**Plan phase:** IMPLEMENTATION_PLAN_REVISION_AWAITING_REVIEW

**Goal:** 将ChatPanel的引用、正文和生成状态迁移为assistant消息级状态，并通过ActiveRequest同步守卫解决停止生成、迟到事件和旧请求清理竞态。

**Architecture:** 保持现有后端和SSE协议不变，在ChatPanel内部使用可辨识消息联合类型、消息ID定向更新函数和ActiveRequest对象身份守卫。React消息状态负责渲染和终态一致性，ref内的stopped标志负责同步阻断停止后的缓冲事件。

**Tech Stack:** React 19、Next.js 16、TypeScript、Jest 30、Testing Library、user-event、现有streamQA异步生成器接口。

**Design spec:** `docs/superpowers/specs/2026-08-02-e003-message-scoped-citations-design.md`（已批准）

**Base:** `origin/dev` @ `3408b41ad0eacfd99bdb1c8352581e217dbbac6e`（计划修订时 merge-base 一致，无漂移；实施 Task 0 必须复核）

**Branch / Worktree:** `fix/e003-message-scoped-citations` @ `D:\administrator\Desktop\大模型产品化\port-knowledge-base-e003`

## 0. 计划状态

- 设计规格已获用户批准；
- 原计划（commit `a819b21`）经用户审查需要修订，本文件为修订后版本；
- **本次修订不等于实施授权**；修订后仍需用户再次审阅，审阅通过后由独立实施会话按 superpowers:executing-plans 单会话逐任务执行；
- 相对原计划的七项修订：
  1. 取消全部 RED 提交与带已知缺陷的提交点，收敛为三个绿色实施提交（§2、§7、§8、§10）；
  2. 两轮测试使用相同引用编号 `[1]` 映射不同文档，证明引用数组按消息隔离（§5、§7）；
  3. `isWritableRequest` 改为 `useCallback` 并进入 `handleSubmit` 依赖数组（§8.3）；
  4. AbortError 兜底路径增加请求对象身份检查，明确三种 AbortError 路径（§8.3）；
  5. 恢复完整后端 pytest 与数据目录零污染 manifest 验证（§6、§10）；
  6. 补齐 stopped history、HTTP 错误事件契约、正常 entity、流式交互门禁、第二轮错误不覆盖第一轮引用等回归测试（§8.2）；
  7. 补齐 PR、CI、Codex、合并与清理治理 Task（§11）。

## 1. Global Constraints

- 唯一生产代码变更文件为 `frontend/src/components/ChatPanel.tsx`；
- 新建测试为 `frontend/tests/unit/ChatPanel.test.tsx`；
- 不修改后端、SSE协议、ChatBubble、ThoughtTrace、qa-stream、package.json、Jest配置或CI；
- 使用TDD：生产代码前必须先新鲜运行目标测试并确认RED，RED 不提交（详见 §2）；
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
- thoughts仍为当前轮临时状态，不持久化到历史消息；
- Git和PR遵循 `docs/dev/GIT_WORKFLOW.md`；所有Git写操作仅在实施任务合同明确授权后执行；
- 后续实施不使用多Agent，单会话逐任务执行；
- `npm run build` 与 ESLint 必须分别执行，不得声称build包含lint；
- 实施在 E003 worktree 内进行，不新建worktree、不切换分支；
- 命令工作目录：前端 npm/Jest/ESLint/build 命令在 `frontend/` 目录执行；pytest、数据 manifest、Git 与仓库范围核验命令在仓库根目录执行；
- 实施不得写入 `originals/`、`raw/`、`wiki/`、`meta/` 四个数据目录，以 before/after manifest 断言零污染（§6、§10）。

## 2. TDD 与提交原则

本计划所有实施 Task 遵循：

```text
写失败测试
→ 新鲜运行并记录RED
→ 不提交
→ 写满足该组测试的最小完整实现
→ 新鲜运行GREEN及相关回归
→ 测试和生产代码一起提交
```

任何Git提交都必须同时满足：

- 当前目标测试绿色；
- 既有相关测试绿色；
- 不包含明知下一任务才修复的生产缺陷；
- 不包含故意失败的测试；
- 是可独立审查的完整交付物。

实施提交边界（三个）：

1. `fix: scope citations to assistant messages` —— 同时包含多轮引用测试和消息级引用实现；提交前目标测试与全部Jest绿色；
2. `fix: isolate stopped and stale chat requests` —— 同时包含停止语义、T-R1/T-R2/T-R3/T-R4 及其他请求生命周期测试，以及完整 ActiveRequest、handleStop、done、catch、finally 最终实现；不允许先提交带错误 finally 的代码；提交前目标测试与全部Jest绿色；
3. `docs: record E003 message state isolation` —— 任务文档和 CLAUDE.md 最小更新；所有本地验证新鲜通过后提交。

可以在第 1 和第 2 提交之间增加一个纯测试回归提交，但只能包含已经全部绿色的测试，且必须解释独立审查价值；默认不增加。

## 3. 文件地图

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
  - 本计划，只在计划（修订）阶段修改；实施阶段不修改。

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

## 4. 已核实的代码事实（实施依据，行号对应当前 Head a819b21）

- `ChatPanel.tsx:10-14` Message 仅 `{id, role, content}`；`:30` 组件级 `citations` state；`:70` 新轮 `setCitations([])`；`:99` source 事件全局覆盖；`:197` 渲染统一传全局 citations；`:35` `abortRef`；`:226-237` 停止按钮 `abortRef.current?.abort()`；`:120-135` catch/finally 对所有请求一视同仁地更新；
- `qa-stream.ts:13-18` QAEvent 五类事件；`:68-73` `streamQA(query, history, apiBase, signal)`；`:81-85` HTTP 非2xx以 delta+done 下发错误文本，不抛异常；
- `ChatBubble.tsx:25-41` `linkifyCitations` 按 props citations 解析；CitationTrigger testid `citation-trigger`；Tooltip 链接 testid `citation-link-<doc_id>`、href `/wiki/<doc_id>`；
- `ThoughtTrace.tsx:23` `thoughts.length === 0 && !isStreaming` 时隐藏；step testid `thought-step-<step>`；
- `jest.config.js:20` react-markdown 由 `__mocks__/reactMarkdown.tsx` 替代；`jest.setup.js` 已 mock next/navigation 与 next/image（next/link 不 mock，jsdom 下渲染为 `<a>`）；
- `ChatBubble.test.tsx` 为现有唯一组件级单测（7 项），只断言 trigger，不做 hover；
- E003 worktree 当前无 `frontend/node_modules`，实施 Task 0 必须 `npm ci`；
- CLAUDE.md §15 记录本机 Python 为 `D:\ProgramData\anaconda3\python.exe`；实施时以 `Get-Command python` 核验实际解释器并记录，不得假设其他机器使用相同路径。

## 5. 受控异步流测试基础设施（Task 1 建立，全文件复用）

设计要点：

- mock `@/lib/qa-stream` 模块，仅替换 `streamQA` 为 `jest.fn`；
- 每轮提问前 `queueStream()` 入队一条受控流；mock 实现按调用顺序出队，并把第4参数 `signal` 记录到 `stream.signal`，使 controller abort 可观测；
- `emit` 同步投递事件给正在等待的 `next()`（或排队）；`fail(error)` 使当前及后续 `next()` 拒绝；`close()` 使迭代正常结束；
- 不在 helper 内自动把 signal abort 转成拒绝——停止竞态测试要求"点击停止后、AbortError 到达前"由测试显式控制迟到事件注入，最后才显式 `fail(abortError())`；
- `afterEach` 释放全部已创建流的 gate，防止 Jest 悬挂；
- **两轮引用使用相同编号 `[1]`**：`DOC_A` 与 `DOC_B` 的 `ref` 都是 `'[1]'`，仅 `doc_id` 不同。相同引用文本 `[1]` 出现在不同 assistant 消息正文中，每条消息必须使用自己的 citations 数组解析；当前全局状态缺陷会把第一条 `[1]` 错误映射为 `doc_B`，修复后两条消息互不影响。

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

// 关键:两轮引用使用相同编号 [1],仅 doc_id 不同。
// 相同引用文本 [1] 在不同 assistant 消息中必须各自解析:
// 第一条的 [1] → /wiki/doc_A;第二条的 [1] → /wiki/doc_B。
const DOC_A: CitationMeta[] = [
    {
        ref: '[1]',
        doc_id: 'doc_A',
        title: '岸桥远控技术方案',
        section: '第3章 网络方案',
    },
];
const DOC_B: CitationMeta[] = [
    {
        ref: '[1]',
        doc_id: 'doc_B',
        title: '5G港口网络规划',
        section: '第7页 表2',
    },
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

## 6. Task 0: Preflight、依赖、基线和数据 manifest

**Files:** 无变更（只读 + 环境准备）。**不提交。**

**Interfaces:** Consumes: `frontend/package.json`、CI `frontend-unit-build` 命令契约、CLAUDE.md §15 解释器事实。Produces: 可用 node_modules、绿色基线、数据目录 before manifest、Git 基线记录。

- [ ] 复核 Git 基线（仓库根目录）：`git branch --show-current` 为 `fix/e003-message-scoped-citations`；`git status --short` clean；`git fetch origin --prune` 后 `git merge-base origin/dev HEAD` 仍为 `3408b41...`；若 origin/dev 已前进，停止并向用户报告，不擅自 merge；
- [ ] 确认实施任务合同已明确授权 Git 写操作（commit）；未授权时只执行到本地验证并报告；
- [ ] 核验 Python 解释器（仓库根目录）：`Get-Command python | Select-Object -ExpandProperty Source`，记录实际路径（CLAUDE.md §15 记载本机为 `D:\ProgramData\anaconda3\python.exe`）；
- [ ] `cd frontend && npm ci`（worktree 无 node_modules）；
- [ ] 基线回归（frontend 目录）：`npm test -- --runInBand` —— 现有全部测试必须通过（含 ChatBubble.test.tsx 7 项），记录通过/失败/跳过数量作为基线；**若基线 Jest 失败，停止，不开始 E003**；
- [ ] 生成数据目录 before manifest（仓库根目录，输出到仓库外 `$env:TEMP\port-kb-e003-data-before.json`，字段 `relative_path | size | mtime_ns`，覆盖 `originals/`、`raw/`、`wiki/`、`meta/`）：

```powershell
python -c "import os,json;roots=['originals','raw','wiki','meta'];items=[{'relative_path':(p:=os.path.join(dp,f)).replace(os.sep,'/'),'size':os.path.getsize(p),'mtime_ns':os.stat(p).st_mtime_ns} for r in roots for dp,_,fs in os.walk(r) for f in fs];items.sort(key=lambda x:x['relative_path']);json.dump(items,open(os.path.join(os.environ['TEMP'],'port-kb-e003-data-before.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1);print('before files:',len(items))"
```

- [ ] 不得在仓库内生成 manifest 文件；manifest 只写入 `$env:TEMP`；
- [ ] 记录 Git 数据目录基线（仓库根目录）：`git status --short`、`git diff --name-only`、`git diff --cached --name-only`，并记录当前已跟踪数据目录状态（`git ls-files originals raw wiki meta | Measure-Object -Line`）；
- [ ] 记录基线事实到实施会话笔记（供 Task 3 任务文档引用）。

**Verify:** `npm test -- --runInBand` 退出码 0；before manifest 文件存在于 `$env:TEMP`。

**Commit:** 无。

---

## 7. Task 1: 多轮引用隔离 RED → GREEN → 绿色提交

**Files:**
- Create: `frontend/tests/unit/ChatPanel.test.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`

**Interfaces:** Consumes: ChatPanel 现有 props（`onHighlight?`）与 testid 契约（`chat-input`/`chat-submit`/`chat-stop`/`chat-bubble`/`citation-trigger`/`citation-link-<doc_id>`）、`streamQA` 模块接口与 `QAEvent`/`CitationMeta` 类型。Produces: `ControlledQAStream` 测试 helper、5 个多轮引用测试、消息联合类型与消息级 citations/status 实现。

### 7.1 写测试（不提交）

- [ ] 创建 `frontend/tests/unit/ChatPanel.test.tsx`，写入 §5 完整 helper 代码；
- [ ] 追加 `describe('多轮问答消息级引用', ...)`。**测试意图说明（写入文件注释）：两轮引用使用相同编号 `[1]` 映射不同文档；每条 assistant 消息必须用自己的 citations 数组解析正文中的 `[1]`；当前组件级全局 citations 缺陷会把第一条的 `[1]` 错误映射为 `doc_B`。**完整测试代码：

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
        await s2.emit({ type: 'delta', text: '第二轮正文[1]。' });
        await s2.emit({ type: 'done' });
        await screen.findByText(/第二轮正文/);

        const bubbles = assistantBubbles();
        expect(within(bubbles[1]).getByTestId('citation-trigger')).toHaveTextContent('[1]');
        // history 协议不变:只含 role/content,正文含第一轮文本
        expect(mockedStreamQA.mock.calls[1][1]).toEqual([
            { role: 'user', content: '第一轮' },
            { role: 'assistant', content: '第一轮正文[1]。' },
        ]);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T1.3 相同[1]在两条消息中独立映射:第一条仍指向docA(核心回归)', async () => {
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
        await s2.emit({ type: 'delta', text: '第二轮正文[1]。' });
        await s2.emit({ type: 'done' });
        await screen.findByText(/第二轮正文/);

        const bubbles = assistantBubbles();
        // 两条消息正文都含 [1],各自渲染一个 trigger
        expect(within(bubbles[0]).getByTestId('citation-trigger')).toHaveTextContent('[1]');
        expect(within(bubbles[1]).getByTestId('citation-trigger')).toHaveTextContent('[1]');
        // 核心断言:第一条的 [1] 仍指向 docA,不被第二轮的全局覆盖错误映射为 docB
        await user.hover(within(bubbles[0]).getByTestId('citation-trigger'));
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

    test('T1.5 两条消息的[1]跳转各自指向对应文档', async () => {
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
        await s2.emit({ type: 'delta', text: '第二轮正文[1]。' });
        await s2.emit({ type: 'done' });
        await screen.findByText(/第二轮正文/);

        const bubbles = assistantBubbles();
        // 第二条 [1] → doc_B
        await user.hover(within(bubbles[1]).getByTestId('citation-trigger'));
        const linkB = await screen.findByTestId('citation-link-doc_B', undefined, { timeout: 3000 });
        expect(linkB).toHaveAttribute('href', '/wiki/doc_B');
        await user.unhover(within(bubbles[1]).getByTestId('citation-trigger'));
        // 等待第二条 Tooltip 退场,避免 portal 并存导致断言歧义
        await waitFor(
            () => expect(screen.queryByTestId('citation-link-doc_B')).not.toBeInTheDocument(),
            { timeout: 3000 },
        );
        // 第一条 [1] → doc_A
        await user.hover(within(bubbles[0]).getByTestId('citation-trigger'));
        const linkA = await screen.findByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(linkA).toHaveAttribute('href', '/wiki/doc_A');
    });
});
```

- [ ] **RED 运行（frontend 目录）**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand`；
- [ ] 记录 RED 形态（预期：T1.1、T1.2 在当前代码上**通过**——单轮/末轮恰好吃到全局 citations；T1.3 **失败**——相同 `[1]` 被全局 DOC_B 覆盖，第一条 hover 出现的是 `citation-link-doc_B` 而非 `doc_A`；T1.4 **失败**——`setCitations([])` 清空全部历史引用；T1.5 **失败**——第一条 hover 不出现 `citation-link-doc_A`）。若实际 RED 形态与此不符，停止并核对 helper 正确性后再继续；
- [ ] 确认测试失败原因是生产缺陷而非测试错误（失败断言应落在 citation link/trigger 映射错误，而非 helper 抛错）；
- [ ] **不提交。继续 7.2 实施至 GREEN。**

### 7.2 最小完整实现（消息模型与消息级写入）

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
- [ ] 事件循环改写：`source` → `updateStreamingAssistant(assistantId, m => ({ ...m, citations: event.citations }))`；`delta` → `updateStreamingAssistant(assistantId, m => ({ ...m, content: m.content + event.text }))`（保留 `scrollToBottom()`）；`done` → `updateStreamingAssistant(assistantId, m => ({ ...m, status: 'completed' }))`，本轮暂保留现有 `setIsStreaming(false)` 与现有 catch/finally（停止与请求身份语义在 Task 2 一次性实现为最终形态，本提交交付物完整覆盖"多轮引用隔离"目标，不包含已知缺陷的停止路径改动）；`thought`/`entity` 不变；
- [ ] 渲染处（:197）改为 `citations={msg.role === 'assistant' ? msg.citations : []}`；
- [ ] `handleSubmit` 的 `useCallback` 依赖数组补充 `updateStreamingAssistant`；
- [ ] history 收集代码（:63-66）保持原样（联合类型下 `role`/`content` 均存在，编译与协议不变）；
- [ ] **GREEN 运行（frontend 目录）**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand` —— T1.1–T1.5 全部通过；
- [ ] 若 T1.3/T1.5 的 hover 断言在 jsdom 下不稳定（Radix Tooltip 未打开）：仅允许把 `findByTestId` 的 timeout 提高至 5000 重试一次；仍失败则停止并报告，不得修改 ChatBubble 或 mock 绕过；
- [ ] **全部 Jest 回归（frontend 目录）**：`npm test -- --runInBand` 全部通过（含 ChatBubble.test.tsx 既有 7 项）；
- [ ] 暂存核验（仓库根目录）：`git status --short`、`git diff --check`，变更仅为两个目标文件。

### 7.3 绿色提交

```powershell
git add `
  frontend/src/components/ChatPanel.tsx `
  frontend/tests/unit/ChatPanel.test.tsx

git commit -m "fix: scope citations to assistant messages"
```

**RED command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`（预期失败形态见 7.1，RED 只记录、不提交）

**GREEN command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`

**Regression:** `cd frontend && npm test -- --runInBand`

---

## 8. Task 2: 请求生命周期全部 RED → 最终实现 → 绿色提交

**Files:**
- Modify: `frontend/tests/unit/ChatPanel.test.tsx`（追加两个 describe）
- Modify: `frontend/src/components/ChatPanel.tsx`

**Interfaces:** Consumes: Task 1 的消息模型与 helper、`streamQA` signal 参数、停止按钮 testid `chat-stop`。Produces: `ActiveRequest` 结构、`activeRequestRef`、`useCallback` 版 `isWritableRequest`、`handleStop`、全五类事件身份守卫、done 退出循环、AbortError 身份安全兜底、普通错误身份守卫、finally 对象身份清理、13 个请求生命周期测试。

### 8.1 写全部请求生命周期测试（不提交）

- [ ] 追加 `describe('停止语义与迟到事件守卫', ...)` 与 `describe('请求身份竞态', ...)`。完整测试代码：

```typescript
describe('停止语义与迟到事件守卫', () => {
    test('T2.1 收到source后停止:保留正文和引用,追加一次停止提示', async () => {
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

    test('T2.2 未收到source前停止:引用保持空,正文追加停止提示', async () => {
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

    test('T-R1 点击停止立即建立同步守卫:全部五类迟到事件被忽略', async () => {
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
        // 引用仍是 A([1] 仍映射 doc_A;迟到 source 不得替换)
        expect(within(bubble).getByTestId('citation-trigger')).toHaveTextContent('[1]');
        await user.hover(within(bubble).getByTestId('citation-trigger'));
        const link = await screen.findByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(link).toHaveAttribute('href', '/wiki/doc_A');
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

    test('T2.6 当前请求非显式AbortError:幂等兜底停止一次', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'delta', text: '部分正文' });
        await screen.findByText(/部分正文/);

        // 不点击停止按钮,直接让流抛 AbortError(模拟 reader 自发中止)
        await act(async () => { await s1.fail(abortError()); });

        const [bubble] = assistantBubbles();
        await waitFor(() => expect(bubble.textContent).toMatch(/已停止生成/));
        // 兜底只执行一次
        expect(bubble.textContent!.match(/已停止生成/g)).toHaveLength(1);
        expect(bubble.textContent).toContain('部分正文');
        expect(screen.getByTestId('chat-input')).toBeEnabled();
    });

    test('T2.8 HTTP错误事件契约(delta+done):错误文本正常完成,输入恢复', async () => {
        // 本测试验证 ChatPanel 对 streamQA 的 HTTP 错误事件契约保持兼容;
        // 不声称覆盖 fetch 或 qa-stream 内部 HTTP 分支(那是 qa-stream 的职责)。
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        // 模拟 streamQA 对 HTTP 非 2xx 的组件侧契约:delta 错误文本 + done
        await s1.emit({ type: 'delta', text: '⚠️ 请求失败 (500)' });
        await s1.emit({ type: 'done' });

        await screen.findByText(/⚠️ 请求失败 \(500\)/);
        const [bubble] = assistantBubbles();
        // 不进入 catch 的错误文本语义
        expect(bubble.textContent).not.toContain('⚠️ 流式请求失败');
        // 消息按正常 done 完成:输入恢复、停止按钮消失
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        expect(screen.queryByTestId('chat-stop')).not.toBeInTheDocument();
        // 下一轮可正常发送
        const s2 = queueStream();
        await askQuestion(user, '下一轮');
        await s2.emit({ type: 'delta', text: '正常回答' });
        await screen.findByText(/正常回答/);
        await s2.emit({ type: 'done' });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T2.9 当前请求的entity事件调用onHighlight', async () => {
        const user = userEvent.setup();
        const onHighlight = jest.fn();
        render(<ChatPanel onHighlight={onHighlight} />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'entity', ids: ['doc_A', 'doc_B'] });
        await waitFor(() => expect(onHighlight).toHaveBeenCalledTimes(1));
        expect(onHighlight).toHaveBeenCalledWith(['doc_A', 'doc_B']);
        await s1.emit({ type: 'done' });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T2.10 流式交互门禁:进行中禁用输入,结束后恢复', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '问题');
        await s1.emit({ type: 'delta', text: '流式正文' });
        await screen.findByText(/流式正文/);

        // 流式进行中
        expect(screen.getByTestId('chat-input')).toBeDisabled();
        expect(screen.getByTestId('chat-submit')).toBeDisabled();
        expect(screen.getByTestId('chat-stop')).toBeInTheDocument();
        expect(screen.getByText('推理中')).toBeInTheDocument();

        // 正常 done 后
        await s1.emit({ type: 'done' });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        expect(screen.getByTestId('chat-submit')).toBeDisabled(); // 空输入仍禁用(独立契约)
        expect(screen.queryByTestId('chat-stop')).not.toBeInTheDocument();
        expect(screen.queryByText('推理中')).not.toBeInTheDocument();

        // 输入新文本后发送按钮启用
        await user.type(screen.getByTestId('chat-input'), '新问题');
        expect(screen.getByTestId('chat-submit')).toBeEnabled();
    });
});

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
        // 第一轮消息保持停止终态,不被旧路径改写
        const bubbles = assistantBubbles();
        expect(bubbles[0].textContent).toContain('已停止生成');
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

    test('T2.5 当前请求的普通错误:整体替换为错误文本(现状产品行为)', async () => {
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

    test('T2.7 stopped正文进入下一轮history,且history只含role/content', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        const s1 = queueStream();
        await askQuestion(user, '第一轮问题');
        await s1.emit({ type: 'delta', text: '部分正文' });
        await screen.findByText(/部分正文/);
        await user.click(screen.getByTestId('chat-stop'));
        await act(async () => { await s1.fail(abortError()); });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());

        const s2 = queueStream();
        await askQuestion(user, '第二轮问题');
        await s2.emit({ type: 'delta', text: '第二轮正文' });
        await screen.findByText(/第二轮正文/);

        const history = mockedStreamQA.mock.calls[1][1];
        expect(history).toEqual([
            { role: 'user', content: '第一轮问题' },
            { role: 'assistant', content: '部分正文\n\n⏹ *已停止生成*' },
        ]);
        // history 项只含 role/content;不含 citations/status/assistantId/thoughts
        for (const turn of history!) {
            expect(Object.keys(turn).sort()).toEqual(['content', 'role']);
        }
        await s2.emit({ type: 'done' });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
    });

    test('T2.11 第二轮错误不得覆盖第一轮引用', async () => {
        const user = userEvent.setup();
        render(<ChatPanel />);
        // 第一轮正常完成,引用 doc_A
        const s1 = queueStream();
        await askQuestion(user, '第一轮');
        await s1.emit({ type: 'source', citations: DOC_A });
        await s1.emit({ type: 'delta', text: '第一轮正文[1]。' });
        await s1.emit({ type: 'done' });
        await screen.findByText(/第一轮正文/);
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        // 第二轮普通错误
        const s2 = queueStream();
        await askQuestion(user, '第二轮');
        await s2.emit({ type: 'delta', text: '第二轮部分正文' });
        await screen.findByText(/第二轮部分正文/);
        await act(async () => { await s2.fail(new Error('boom')); });
        await waitFor(() => expect(screen.getByTestId('chat-input')).toBeEnabled());
        // 第一轮的 [1] 仍映射 doc_A
        const bubbles = assistantBubbles();
        await user.hover(within(bubbles[0]).getByTestId('citation-trigger'));
        const link = await screen.findByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(link).toHaveAttribute('href', '/wiki/doc_A');
    });
});
```

- [ ] **RED 运行（frontend 目录）**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand`；
- [ ] 记录当前 Task 1 实现下的真实 RED/回归锁定结果（预期：T-R1 **失败**——停止后迟到事件仍写入；T-R2 **失败**——停止后输入未立即恢复、旧 finally 无差别清理；T-R3 **失败**——旧 catch 把已停止的第一轮正文改写为 `⚠️ 流式请求失败`；T2.1、T2.2、T2.5、T2.6、T2.7、T2.8、T2.9、T2.10、T-R4、T2.11 为现状行为回归锁定，**通过**）。若实际形态不符，停止核对；
- [ ] **不提交。继续 8.3 一次性实现最终形态至 GREEN。**

### 8.2 错误路径 citations 保留的验证边界（明确写入任务文档）

当前请求普通错误时 `message.citations` 保留已收到值：错误正文被整体替换、不再含 `[1]`，ChatBubble 不再渲染 trigger，citations 数组保留**无法通过 UI 直接证明**。本计划明确：

- citations 保留由生产更新中的 `...message` 展开保证（错误更新只覆盖 `content` 与 `status`），由代码审查和 TypeScript 类型实现确认；
- 不为不可见内部状态增加测试专用生产接口；
- 用户可观察部分由 T2.11 覆盖（第二轮错误不得覆盖第一轮引用，UI 断言）。

### 8.3 一次性实现最终形态（不允许已知缺陷的交付）

按以下顺序修改 `ChatPanel.tsx`（本任务的最终实现顺序，直接落最终代码）：

- [ ] 1. 确认 `AssistantStatus`、`UserMessage`、`AssistantMessage`、`Message` 联合类型已存在（Task 1 已建）；
- [ ] 2. 定义 `ActiveRequest`（组件外模块级）：

```typescript
interface ActiveRequest {
    assistantId: string;
    controller: AbortController;
    stopped: boolean;
}
```

- [ ] 3. 删除 `abortRef`（:35），替换为：

```typescript
const activeRequestRef = useRef<ActiveRequest | null>(null);
```

- [ ] 4. 确认 `updateAssistant`（`useCallback(..., [])`）与 `updateStreamingAssistant`（`useCallback(..., [updateAssistant])`）已存在（Task 1 已建）；
- [ ] 5. 定义 `isWritableRequest` 为 `useCallback`（读取 ref 不需依赖，但必须稳定且进入依赖数组）：

```typescript
/** 最强守卫:对象身份 + 同步停止标志(ref 内同步可读,不等待 React 状态提交) */
const isWritableRequest = useCallback(
    (request: ActiveRequest): boolean =>
        activeRequestRef.current === request &&
        !request.stopped,
    [],
);
```

- [ ] 6. 定义 `handleStop`（`useCallback`，依赖 `[updateStreamingAssistant]`；顺序固定：先同步 flag、再消息终态、再解除 UI 流式、最后 abort）：

```typescript
const handleStop = useCallback(() => {
    const request = activeRequestRef.current;
    if (!request || request.stopped) return; // 重复点击幂等:停止提示最多一次
    // 1. 同步建立竞态守卫(必须先于 abort,不等待 React 状态提交)
    request.stopped = true;
    // 2. 消息终态:保留已有正文与引用,追加一次停止提示
    //    (经 updateStreamingAssistant:已完成的消息不会被改回 stopped)
    updateStreamingAssistant(request.assistantId, message => ({
        ...message,
        content:
            message.content +
            (message.content ? '\n\n' : '') +
            '⏹ *已停止生成*',
        status: 'stopped',
    }));
    // 3. 立即恢复输入界面
    setIsStreaming(false);
    // 4. 最后中止底层流
    request.controller.abort();
}, [updateStreamingAssistant]);
```

- [ ] 7. `handleSubmit` 内创建局部 request 对象并发布到 ref（替换 controller 直存）：

```typescript
const controller = new AbortController();
const request: ActiveRequest = { assistantId, controller, stopped: false };
activeRequestRef.current = request;
```

- [ ] 8. 五类事件统一身份检查 + done 真正退出循环（事件循环最终形态）：

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
                // 只标记消息终态;isStreaming 与 ref 清理由 finally 统一执行
                updateStreamingAssistant(assistantId, m => ({ ...m, status: 'completed' }));
                break;
        }
        if (event.type === 'done') break; // 记录已收到 done:退出事件循环,交给 finally 收口
    }
}
```

- [ ] 9. catch 最终身份安全实现（三种 AbortError 路径 + 普通错误守卫）：

```typescript
} catch (err) {
    const isAbort = err instanceof Error && err.name === 'AbortError';
    if (isAbort) {
        // 8.1 用户显式停止:request.stopped 已为 true,仅吞掉,不追加第二次停止提示;
        // 8.3 旧请求 AbortError:对象身份不匹配,完全忽略,不改旧/新消息、isStreaming 或 ref;
        // 8.2 当前请求非显式 AbortError(如 reader 自发中止):执行一次幂等兜底停止。
        if (
            activeRequestRef.current === request &&
            !request.stopped
        ) {
            request.stopped = true;

            updateStreamingAssistant(request.assistantId, message => ({
                ...message,
                content:
                    message.content +
                    (message.content ? '\n\n' : '') +
                    '⏹ *已停止生成*',
                status: 'stopped',
            }));

            setIsStreaming(false);
        }
    } else if (activeRequestRef.current === request && !request.stopped) {
        // 仅当前未停止请求的失败允许标记 error
        // (整体替换正文为错误文本的现状产品行为不变;...m 展开保留已收到 citations)
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

- [ ] 10. 停止按钮 `onClick={() => abortRef.current?.abort()}`（:230）改为 `onClick={handleStop}`；
- [ ] 11. Hooks 依赖数组完整（不通过禁用 eslint 规则隐藏依赖）：
  - `updateAssistant`：`useCallback(..., [])`；
  - `updateStreamingAssistant`：`useCallback(..., [updateAssistant])`；
  - `isWritableRequest`：`useCallback(..., [])`；
  - `handleStop`：`useCallback(..., [updateStreamingAssistant])`；
  - `handleSubmit`：至少 `[inputValue, isStreaming, messages, onHighlight, scrollToBottom, updateStreamingAssistant, isWritableRequest]`；
  - 不添加 `eslint-disable react-hooks/exhaustive-deps`；若 touched-file ESLint 要求其他依赖，按实际代码补齐，不压制规则；
- [ ] 12. history 仍只映射 `role`/`content`（:63-66 原样保留）。

本提交**不得**包含以下代码形态：未做请求身份校验的 finally 清理；旧 catch 的按 id map 式整体覆盖；仅靠消息 status 阻断停止竞态而无 `request.stopped` 同步守卫；普通函数版 `isWritableRequest` 且未列入依赖数组。

### 8.4 验证与绿色提交

- [ ] **GREEN 运行（frontend 目录）**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand` —— 全部通过（T1.*、T2.*、T-R*）；
- [ ] **全部 Jest（frontend 目录）**：`npm test -- --runInBand` 全部通过；
- [ ] **touched-file ESLint（frontend 目录）**：`npx eslint src/components/ChatPanel.tsx tests/unit/ChatPanel.test.tsx` —— 退出码 0；
- [ ] **Next 生产构建（frontend 目录）**：`npm run build` —— 通过；
- [ ] 暂存核验（仓库根目录）：`git status --short`、`git diff --check`，变更仅为两个目标文件；
- [ ] 绿色提交：

```powershell
git add `
  frontend/src/components/ChatPanel.tsx `
  frontend/tests/unit/ChatPanel.test.tsx

git commit -m "fix: isolate stopped and stale chat requests"
```

**RED command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`（预期失败形态见 8.1，RED 只记录、不提交）

**GREEN command:** `cd frontend && npx jest tests/unit/ChatPanel.test.tsx --runInBand`

**Regression:** `cd frontend && npm test -- --runInBand`；`cd frontend && npx eslint src/components/ChatPanel.tsx tests/unit/ChatPanel.test.tsx`；`cd frontend && npm run build`

---

## 9. Task 3: 完整仓库验证和文档收口

**Files:**
- Modify: `CLAUDE.md`（§11 末尾一行、§17 第 8 项）
- Create: `docs/dev/tasks/E003-message-scoped-citations.md`

**Interfaces:** Consumes: Task 1–2 全部 GREEN 事实、CI required checks 契约、Task 0 基线与 before manifest。Produces: 完整验证证据、数据零污染断言、任务文档、CLAUDE.md 最小更新。

按以下顺序执行（每项都必须在实施会话新鲜运行并记录退出码与关键输出）：

- [ ] **9.1 前端目标测试（frontend 目录）**：`npx jest tests/unit/ChatPanel.test.tsx --runInBand`；
- [ ] **9.2 全部 Jest（frontend 目录）**：`npm test -- --runInBand`，记录通过/失败/跳过数量；
- [ ] **9.3 touched-file ESLint（frontend 目录）**：

```powershell
npx eslint `
  src/components/ChatPanel.tsx `
  tests/unit/ChatPanel.test.tsx
```

  退出码 0；不得把 build 描述为包含 lint；
- [ ] **9.4 Next 生产构建（frontend 目录）**：`npm run build`；
- [ ] **9.5 后端完整 pytest（仓库根目录）**：`python -m pytest tests/ -q`（解释器以 Task 0 核验的实际 `python` 为准）。**不得因"后端零变更"跳过。**记录：exit code、passed、failed、errors、warnings、执行时长、无Key/离线条件是否适用（本机 `.env` 是否含真实 Key、测试是否按设计不访问真实外部服务）；
- [ ] **9.6 数据 manifest 比较（仓库根目录）**：生成 after manifest 并与 before 比较，字段 `relative_path | size | mtime_ns`，必须完全一致：

```powershell
python -c "import os,json;before=json.load(open(os.path.join(os.environ['TEMP'],'port-kb-e003-data-before.json'),encoding='utf-8'));roots=['originals','raw','wiki','meta'];after=[{'relative_path':(p:=os.path.join(dp,f)).replace(os.sep,'/'),'size':os.path.getsize(p),'mtime_ns':os.stat(p).st_mtime_ns} for r in roots for dp,_,fs in os.walk(r) for f in fs];after.sort(key=lambda x:x['relative_path']);json.dump(after,open(os.path.join(os.environ['TEMP'],'port-kb-e003-data-after.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1);print('MANIFEST_MATCH' if before==after else 'MANIFEST_DIFF');import sys;sys.exit(0 if before==after else 1)"
```

  若任何目录有变化：**停止；不擅自恢复；报告具体差异；不声称数据零污染**；
- [ ] **9.7 Git 数据目录与范围核验（仓库根目录，文档提交前）**：

```powershell
git status --short
git diff --check
git diff --name-status origin/dev
git diff --cached --name-status
```

  说明：`git diff origin/dev..HEAD` 只覆盖已提交内容，不能代替未提交工作区检查，因此此处使用 `git diff --name-status origin/dev` 覆盖工作区+暂存对 base 的全部差异；仅允许任务合同内文件变化；
- [ ] **9.8 创建 `docs/dev/tasks/E003-message-scoped-citations.md`**，必须记录：Base；branch；worktree；设计规格与本计划路径；数据 before/after manifest 结果；基线 Jest；多轮引用 RED 形态与 GREEN 证据；请求生命周期 RED 形态与 GREEN 证据；相同 `[1]` 独立映射证据（T1.3/T1.5）；T-R1 至 T-R4；stopped history（T2.7）；HTTP 错误事件契约（T2.8）；正常 entity（T2.9）；流式交互门禁（T2.10）；全部 Jest 结果；ESLint 结果；build 结果；完整 pytest 结果（exit code/passed/failed/errors/warnings/时长/离线条件）；Git 范围核验输出；PR/CI/Codex/merge 状态字段（实施时为"未执行，等待用户授权"）；Live UAT 未执行；历史 thoughts 仍未持久化（Non-goal 未变）；
- [ ] **9.9 CLAUDE.md 最小更新**（与 E001/E002/T001 的划线+引用体例一致）：
  - §11 末尾"当前 `frontend/src/components/ChatPanel.tsx` 可能存在全局citations覆盖历史回答的已知缺陷，尚未修复。"改为划线形式并追加"（E003已处理：引用、正文和生成状态迁移为assistant消息级状态，ActiveRequest请求身份守卫覆盖停止竞态与旧请求清理；详见 `docs/dev/tasks/E003-message-scoped-citations.md`）"；
  - §17 第 8 项"多轮问答引用可能使用全局状态；"改为划线形式并追加同样的 E003 已处理说明；
- [ ] **9.10 最终新鲜回归（frontend 目录）**：`npm test -- --runInBand` 再次确认绿色（声明必须基于新鲜命令）；
- [ ] **9.11 文档提交（仓库根目录）**：

```powershell
git add `
  CLAUDE.md `
  docs/dev/tasks/E003-message-scoped-citations.md

git commit -m "docs: record E003 message state isolation"
```

- [ ] **9.12 提交后范围核验（仓库根目录）**：

```powershell
git diff origin/dev..HEAD --name-status
git diff origin/dev..HEAD --stat
```

  分支相对 origin/dev 最终允许文件只包括：`CLAUDE.md`、`frontend/src/components/ChatPanel.tsx`、`frontend/tests/unit/ChatPanel.test.tsx`、`docs/dev/tasks/E003-message-scoped-citations.md`、E003 设计规格、E003 实施计划。无 CI、后端、ChatBubble、ThoughtTrace、qa-stream 变化。

**Commit:** `docs: record E003 message state isolation`（9.11）。

---

## 10. Task 4: PR、CI、Codex、合并及清理（仅在用户后续授权后执行，本轮计划修订不执行）

本 Task 只写未来执行流程。实施会话完成 Task 3 后必须停止并报告，等待用户明确授权后才进入本 Task。

- [ ] **10.1 Push（仓库根目录）**：`git push -u origin fix/e003-message-scoped-citations`；
- [ ] **10.2 创建 PR 正文临时文件（仓库外）**：`$env:TEMP\port-kb-e003-pr-body.md`，正文必须包含：根因；消息级 citations/status；ActiveRequest；stop 同步 flag；五类迟到事件守卫；旧 catch/finally 隔离；同编号 `[1]` 测试；Jest/ESLint/build/pytest 结果；数据 manifest 零污染；不改后端和 SSE；不保存历史 thoughts；未执行 Live UAT；
- [ ] **10.3 创建非 Draft PR**：

```powershell
gh pr create `
  --repo fufuharry-ui/port-knowledge-base `
  --base dev `
  --head fix/e003-message-scoped-citations `
  --title "fix: isolate citations and chat request state" `
  --body-file $env:TEMP\port-kb-e003-pr-body.md
```

- [ ] **10.4 检查 PR 元数据**：

```powershell
gh pr view <PR_NUMBER> `
  --repo fufuharry-ui/port-knowledge-base `
  --json number,state,isDraft,baseRefName,headRefName,headRefOid,mergeable,statusCheckRollup,url
```

  必须确认：state=OPEN；isDraft=false；baseRefName=dev；headRefName 正确；headRefOid 等于当前本地 HEAD；
- [ ] **10.5 等待 required checks 全部成功**：`repository-integrity`、`python-core`、`frontend-unit-build`。不得使用 `--admin`、跳过检查、`continue-on-error` 或 `|| true`；
- [ ] **10.6 Codex 审查**：确认 Head 后在 PR 评论 `@codex review`。Codex 结论必须对应当前 headRefOid。审查标准：P0/P1 阻断；经验证成立的 correctness/security/test-isolation/scope-consistency P2 阻断；建议性或风格性问题不自动修改，先验证。若修改代码：新提交 → push → 重新等待全部 checks → 对新 Head 重新触发 `@codex review`；旧 Head 审查不能作为新 Head 通过证据；
- [ ] **10.7 Base 漂移处理**：合并前 `git fetch origin`、`git rev-parse origin/dev`、`git merge-base origin/dev HEAD`。若 dev 前进：`git merge origin/dev`（禁止 rebase、reset、amend、force push）；解决冲突后重新运行全部本地验证（Task 3 的 9.1–9.7）、push、等待全部 checks、对新 Head 重新 Codex 审查；
- [ ] **10.8 合并前 Head 校验**：重新读取 headRefOid 并与本地 HEAD 核实一致：

```powershell
$headSha = gh pr view <PR_NUMBER> `
  --repo fufuharry-ui/port-knowledge-base `
  --json headRefOid `
  --jq '.headRefOid'

git rev-parse HEAD
```

  两者必须一致，不一致时停止合并并重新审查；
- [ ] **10.9 合并**：

```powershell
gh pr merge <PR_NUMBER> `
  --repo fufuharry-ui/port-knowledge-base `
  --merge `
  --match-head-commit $headSha
```

  禁止 `--admin`；
- [ ] **10.10 合并后验证**：PR=MERGED；merge commit 存在；origin/dev 包含该 merge；dev push CI 三项检查全绿；任务文档中 PR/CI/Codex/merge 状态字段更新为最终事实；
- [ ] **10.11 清理**（只有合并及 dev push CI 全绿后）：

```powershell
git worktree remove `
  D:\administrator\Desktop\大模型产品化\port-knowledge-base-e003

git branch -d fix/e003-message-scoped-citations
git push origin --delete fix/e003-message-scoped-citations
git worktree prune
```

  如因锁定无法删除 worktree：报告；不使用 force；不使用 `-D`；不使用 reset/clean；保留现场。

---

## 11. 验收映射（设计验收 → 测试 → 实施 Task → 验证命令）

| 验收项 | 测试 | Task | 验证命令 |
|---|---|---|---|
| 两轮相同 `[1]` 独立映射（第一条→doc_A，第二条→doc_B） | T1.3、T1.5 | 1 | `npx jest tests/unit/ChatPanel.test.tsx --runInBand` |
| 新轮开始旧引用不消失 | T1.4 | 1 | 同上 |
| 第一轮引用文档A / 第二轮引用文档B | T1.1、T1.2 | 1 | 同上 |
| history 协议只含 role/content | T1.2、T2.7 | 1、2 | 同上 |
| stop 后保留正文和引用、提示一次 | T2.1 | 2 | 同上 |
| source 前停止引用为空 | T2.2 | 2 | 同上 |
| 停止后无需等待 AbortError 阻止迟到事件 | T-R1 | 2 | 同上 |
| late thought/source/entity/delta/done 全部忽略 | T-R1 | 2 | 同上 |
| 旧 finally 不清理新请求 | T-R2 | 2 | 同上 |
| 旧 catch/异常不污染新请求、不改写已停止消息 | T-R3 | 2 | 同上 |
| 旧请求 AbortError 完全忽略 | T-R2（s1 AbortError 于第二轮进行中） | 2 | 同上 |
| 当前请求非显式 AbortError 幂等兜底 | T2.6 | 2 | 同上 |
| 正常 done 统一收口 | T-R4 | 2 | 同上 |
| 当前请求普通 error 整体替换正文 | T2.5 | 2 | 同上 |
| 错误路径 citations 保留（`...message` 展开，代码审查确认，无测试接口） | T2.11（用户可观察部分） | 2 | 同上 + 代码审查 |
| 第二轮错误不覆盖第一轮引用 | T2.11 | 2 | 同上 |
| stopped 正文进入下一轮 history 且字段纯净 | T2.7 | 2 | 同上 |
| HTTP 错误事件契约（delta+done） | T2.8 | 2 | 同上 |
| 正常 entity 调用 onHighlight | T2.9 | 2 | 同上 |
| 流式交互门禁 | T2.10 | 2 | 同上 |
| thoughts 临时边界（late thought 不入面板、不持久化） | T-R1 | 2 | 同上 |
| 不改后端/SSE/ChatBubble/qa-stream | 范围核验 | 3 | `git diff origin/dev..HEAD --name-status` |
| 全部 Jest 绿色 | 全量回归 | 2、3 | `npm test -- --runInBand` |
| touched-file ESLint 绿色 | — | 2、3 | `npx eslint src/components/ChatPanel.tsx tests/unit/ChatPanel.test.tsx` |
| Next build 绿色 | — | 2、3 | `npm run build` |
| 完整后端 pytest | — | 3 | `python -m pytest tests/ -q`（仓库根目录） |
| 数据目录零污染 | — | 0、3 | before/after manifest 比较 |
| required checks 全绿 | — | 4 | `gh pr checks`（repository-integrity、python-core、frontend-unit-build） |
| Codex 审查对应当前 Head | — | 4 | PR 评论 `@codex review` |
| merge（`--match-head-commit`） | — | 4 | `gh pr merge --merge --match-head-commit` |
| cleanup（worktree/分支） | — | 4 | `git worktree remove`、`git branch -d`、`git push origin --delete` |

## 12. 实施风险与应对

- **Radix Tooltip jsdom hover**：Tooltip.Provider `delayDuration=200` 为真实延迟，由 `findByTestId(..., { timeout: 3000 })` 轮询消化，不使用 fake timers；连续 hover 两条消息时先 `unhover` 并 `waitFor` 旧 portal 退场（T1.5 已内建该步骤）；若仍不稳定，仅允许提高 timeout 至 5000 重试一次，再失败则停止报告（不得修改 ChatBubble/mock/生产代码绕过）；
- **act 警告**：所有 `fail()` 调用包在 `act` 中；`emit` 后接 `findBy`/`waitFor` 自动包裹；出现警告时先调整测试时序，不得屏蔽警告；
- **Jest 悬挂**：`afterEach` 统一 `close()` 全部已创建流；`--runInBand` 运行；若出现悬挂，检查是否有未释放的 pending `next()`；
- **同毫秒 id**：`user-${Date.now()}`/`assistant-${Date.now()}` 前缀不同不冲突；单并发前提下两条 assistant 同毫秒不可能（占位创建在 await 前，下一轮被 isStreaming 阻断）；
- **pytest 环境**：本机 `.env` 可能含真实 Key；后端测试套件按设计不访问真实外部服务（T001 已在无密钥+黑洞代理环境全绿）；Task 3 记录实际环境事实，若本地 pytest 出现与离线设计不符的网络访问迹象，停止并报告；
- **Base 漂移**：Task 0 复核 merge-base；实施期间 origin/dev 前进时停止报告，不擅自 merge；
- **hover 断言与相同 `[1]`**：两条消息的 trigger 文本相同（都是 `[1]`），所有 trigger 断言必须先用 `within(bubble)` 或 bubble 索引收窄，禁止使用全局 `getByTestId('citation-trigger')` 断言多轮场景。
