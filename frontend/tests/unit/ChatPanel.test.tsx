/**
 * tests/unit/ChatPanel.test.tsx — ChatPanel 消息级引用与请求竞态 TDD 测试 (E003)
 * streamQA 以 jest.mock 替换为受控异步流;deferred 控制每轮推进,不依赖真实网络与真实 timer。
 *
 * 引用隔离测试意图:两轮引用使用相同编号 [1] 映射不同文档;
 * 每条 assistant 消息必须用自己的 citations 数组解析正文中的 [1];
 * 当前组件级全局 citations 缺陷会把第一条的 [1] 错误映射为 doc_B。
 *
 * 事件消费 ack 机制说明(pull counting):
 * for-await 循环只有在上一次迭代体(含请求身份守卫)执行完毕后才会再次调用
 * iterator.next()。因此 next() 调用次数(pullCount)的递增可以证明:
 * 前一个投递的事件已被组件从迭代器取出、经过循环体处理、并请求了下一个事件。
 * emitAndWaitForNextPull 在每次投递后等待 pullCount 增加,以此确认迟到事件
 * 确实进入了 for-await 循环并被守卫忽略,而不是从未到达组件造成假绿。
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
    /** 投递事件并等待组件再次调用 next()(证明上一事件已被循环体消费处理) */
    emitAndWaitForNextPull(event: QAEvent): Promise<void>;
    fail(error: unknown): Promise<void>;
    close(): Promise<void>;
    signal?: AbortSignal;
}

function createControlledQAStream(): ControlledQAStream {
    type Pending = {
        resolve: (r: IteratorResult<QAEvent>) => void;
        reject: (e: unknown) => void;
    };
    type PullWaiter = { target: number; resolve: () => void };
    const queue: QAEvent[] = [];
    const pending: Pending[] = [];
    const pullWaiters: PullWaiter[] = [];
    let failed: { error: unknown } | null = null;
    let closed = false;
    let pullCount = 0;

    const notePull = () => {
        pullCount += 1;
        for (let i = pullWaiters.length - 1; i >= 0; i--) {
            if (pullCount >= pullWaiters[i].target) {
                pullWaiters[i].resolve();
                pullWaiters.splice(i, 1);
            }
        }
    };

    const waitForPullCount = (target: number): Promise<void> => {
        if (pullCount >= target) return Promise.resolve();
        return new Promise<void>(resolve => {
            pullWaiters.push({ target, resolve });
        });
    };

    const stream: ControlledQAStream = {
        iterable: {
            [Symbol.asyncIterator]() {
                return {
                    next(): Promise<IteratorResult<QAEvent>> {
                        notePull();
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
        async emitAndWaitForNextPull(event: QAEvent) {
            const base = pullCount;
            await stream.emit(event);
            await waitForPullCount(base + 1);
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
    return stream;
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

beforeAll(() => {
    // jsdom 未实现 scrollIntoView(ThoughtTrace 在 effect 中调用);stub 为 no-op
    Element.prototype.scrollIntoView = jest.fn();
    // jsdom 未实现 ResizeObserver(Radix Tooltip use-size 依赖);stub 为 no-op 类
    class ResizeObserverStub {
        observe() {}
        unobserve() {}
        disconnect() {}
    }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;
});

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
        // Radix 过渡期间可能同时渲染两份 portal 内容,取首个
        const links = await screen.findAllByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(links[0]).toHaveAttribute('href', '/wiki/doc_A');
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
        // Radix 过渡期间可能同时渲染两份 portal 内容,取首个
        const linksB = await screen.findAllByTestId('citation-link-doc_B', undefined, { timeout: 3000 });
        expect(linksB[0]).toHaveAttribute('href', '/wiki/doc_B');
        // 注意:jsdom 不触发 animationend,Tooltip 退场副本不会卸载,
        // 因此不能等待旧 portal 消失;直接 hover 第一条并用 doc_A 专属 testid 断言。
        // 第一条 [1] → doc_A
        await user.hover(within(bubbles[0]).getByTestId('citation-trigger'));
        const linksA = await screen.findAllByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(linksA[0]).toHaveAttribute('href', '/wiki/doc_A');
    });
});

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

    test('T-R1 点击停止立即建立同步守卫:全部五类迟到事件被消费但全部忽略', async () => {
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
        // 逐条投递全部五类迟到事件;emitAndWaitForNextPull 等待组件再次调用 next(),
        // 证明每个事件都确实进入 for-await 循环体并被守卫忽略(而非未到达组件的假绿)
        await s1.emitAndWaitForNextPull({ type: 'thought', step: 99, message: '迟到思考' });
        await s1.emitAndWaitForNextPull({ type: 'source', citations: DOC_B });
        await s1.emitAndWaitForNextPull({ type: 'entity', ids: ['doc_B'] });
        await s1.emitAndWaitForNextPull({ type: 'delta', text: '迟到正文' });
        await s1.emitAndWaitForNextPull({ type: 'done' });
        // 现在才让生成器抛 AbortError
        await act(async () => { await s1.fail(abortError()); });

        const [bubble] = assistantBubbles();
        // 引用仍是 A([1] 仍映射 doc_A;迟到 source 不得替换)
        expect(within(bubble).getByTestId('citation-trigger')).toHaveTextContent('[1]');
        await user.hover(within(bubble).getByTestId('citation-trigger'));
        const links = await screen.findAllByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(links[0]).toHaveAttribute('href', '/wiki/doc_A');
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
        const links = await screen.findAllByTestId('citation-link-doc_A', undefined, { timeout: 3000 });
        expect(links[0]).toHaveAttribute('href', '/wiki/doc_A');
    });
});
