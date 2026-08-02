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
