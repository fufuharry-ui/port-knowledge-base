/**
 * tests/unit/wiki-polling.test.tsx — 仪表盘编译轮询与失败 Toast 测试 (E004 Task 8)
 * 验证:
 *  - 有 raw/compiling 文档时每 3 秒条件轮询,全部终态后停止;
 *  - 首轮历史 error 不弹 Toast;
 *  - compiling → error 每轮失败只弹一次固定安全文案 Toast;
 *  - 重编译开启新一轮失败轮,可再次 Toast;
 *  - 409 冲突只展示固定文案,不泄露状态码/原始信息。
 */
import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import WikiPage from '@/app/wiki/page';
import { deleteDoc, fetchWikiIndex, recompileDoc, type WikiIndexData } from '@/lib/api';

const mockToastPush = jest.fn();

jest.mock('@/components/ui/Toast', () => ({
    useToast: () => ({ push: mockToastPush }),
}));

jest.mock('@/lib/api', () => {
    const actual = jest.requireActual('@/lib/api');
    return {
        ...actual,
        fetchWikiIndex: jest.fn(),
        deleteDoc: jest.fn(),
        recompileDoc: jest.fn(),
    };
});

const mockedFetch = fetchWikiIndex as jest.MockedFunction<typeof fetchWikiIndex>;
const mockedRecompile = recompileDoc as jest.MockedFunction<typeof recompileDoc>;
const mockedDelete = deleteDoc as jest.MockedFunction<typeof deleteDoc>;

/** 可控 Promise:精确决定每个 fetch 响应何时落地(无真实等待) */
function createDeferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

async function flushMicrotasks(times = 3) {
    for (let i = 0; i < times; i += 1) {
        await Promise.resolve();
    }
}

describe('Wiki 仪表盘编译轮询与失败 Toast (E004 Task 8)', () => {
    beforeEach(() => {
        mockedFetch.mockReset();
        mockedRecompile.mockReset();
        mockedDelete.mockReset();
        mockToastPush.mockReset();
    });

    afterEach(() => {
        jest.clearAllTimers();
        jest.useRealTimers();
    });

    test('polls after 3000ms while a document is compiling', async () => {
        jest.useFakeTimers();
        mockedFetch
            .mockResolvedValueOnce({
                total_docs: 1,
                documents: [{ id: 'doc_1', status: 'compiling' }],
            })
            .mockResolvedValueOnce({
                total_docs: 1,
                documents: [{ id: 'doc_1', status: 'compiled' }],
            });

        render(<WikiPage />);
        await screen.findByTestId('compiling-hint');
        expect(mockedFetch).toHaveBeenCalledTimes(1);

        await act(async () => {
            jest.advanceTimersByTime(3000);
            await Promise.resolve();
        });

        await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));
        expect(screen.queryByTestId('compiling-hint')).toBeNull();

        // 全部进入终态后:再推进 6000ms 也不应再有轮询请求
        await act(async () => {
            jest.advanceTimersByTime(6000);
            await Promise.resolve();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(2);
    });

    test('initial historical error shows no toast', async () => {
        mockedFetch.mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'error', error_code: 'timeout' }],
        });
        render(<WikiPage />);
        await screen.findByTestId('compile-error-message');
        expect(mockToastPush).not.toHaveBeenCalled();
    });

    test('compiling to error emits one friendly toast only', async () => {
        jest.useFakeTimers();
        mockedFetch
            .mockResolvedValueOnce({
                total_docs: 1,
                documents: [{ id: 'doc_1', status: 'compiling' }],
            })
            .mockResolvedValue({
                total_docs: 1,
                documents: [{ id: 'doc_1', status: 'error', error_code: 'timeout' }],
            });
        render(<WikiPage />);
        await screen.findByTestId('compiling-hint');

        await act(async () => {
            jest.advanceTimersByTime(3000);
            await Promise.resolve();
        });
        await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(1));
        expect(mockToastPush).toHaveBeenCalledWith(
            '编译服务响应超时，请稍后重试',
            'error',
        );

        // 同一轮失败终态:继续轮询窗口内不得重复 Toast
        await act(async () => {
            jest.advanceTimersByTime(6000);
            await Promise.resolve();
        });
        expect(mockToastPush).toHaveBeenCalledTimes(1);
    });

    test('a retry starts a new failure round and may toast again', async () => {
        jest.useFakeTimers();
        mockedFetch
            .mockResolvedValueOnce({
                total_docs: 1,
                documents: [{
                    id: 'doc_1',
                    title: 'T',
                    status: 'error',
                    error_code: 'timeout',
                }],
            })
            .mockResolvedValueOnce({
                total_docs: 1,
                documents: [{
                    id: 'doc_1',
                    title: 'T',
                    status: 'error',
                    error_code: 'timeout',
                }],
            })
            .mockResolvedValueOnce({
                total_docs: 1,
                documents: [{
                    id: 'doc_1',
                    title: 'T',
                    status: 'error',
                    error_code: 'compile_failed',
                }],
            });
        mockedRecompile.mockResolvedValue({ status: 'recompiling' });

        render(<WikiPage />);
        const firstButton = await screen.findByTestId('recompile-btn');
        fireEvent.click(firstButton);
        await screen.findByTestId('compiling-hint');

        await act(async () => {
            jest.advanceTimersByTime(3000);
            await Promise.resolve();
        });
        await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(1));

        fireEvent.click(await screen.findByTestId('recompile-btn'));
        await screen.findByTestId('compiling-hint');
        await act(async () => {
            jest.advanceTimersByTime(3000);
            await Promise.resolve();
        });

        await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(2));
        expect(mockToastPush).toHaveBeenLastCalledWith(
            '编译失败，请稍后重试或联系管理员',
            'error',
        );
    });

    test('duplicate recompile shows friendly conflict copy only', async () => {
        const { ApiError } = jest.requireActual('@/lib/api');
        mockedFetch.mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', title: 'T', status: 'compiled' }],
        });
        mockedRecompile.mockRejectedValue(
            new ApiError(
                '该文档正在编译，请稍后再试',
                409,
                'compile_in_progress',
            ),
        );

        render(<WikiPage />);
        fireEvent.click(await screen.findByTestId('recompile-btn'));

        await waitFor(() => {
            expect(mockToastPush).toHaveBeenCalledWith(
                '该文档正在编译，请稍后再试',
                'error',
            );
        });
        expect(mockToastPush.mock.calls.flat().join(' ')).not.toMatch(
            /409|Conflict|RuntimeError|OPENAI_API_KEY/,
        );
    });

    test('delete during an active compile shows fixed friendly copy only', async () => {
        // E004-FIX-02:删除与编译事务互斥——后端 409 knowledge_base_busy 只显示固定文案
        const { ApiError, getCompileErrorMessage } = jest.requireActual('@/lib/api');
        mockedFetch.mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', title: 'T', status: 'compiled' }],
        });
        mockedDelete.mockRejectedValue(
            new ApiError(
                getCompileErrorMessage('knowledge_base_busy'),
                409,
                'knowledge_base_busy',
            ),
        );

        render(<WikiPage />);
        fireEvent.click(await screen.findByTestId('delete-btn'));
        fireEvent.click(await screen.findByTestId('confirm-accept'));

        await waitFor(() => {
            expect(mockToastPush).toHaveBeenCalledWith(
                '知识库正在执行编译任务，请稍后再删除',
                'error',
            );
        });
        expect(mockToastPush.mock.calls.flat().join(' ')).not.toMatch(
            /409|Conflict|Lock|threading|COMPILE_EXECUTION_LOCK/,
        );
    });
});

/**
 * E004-FIX-01:重编译点击前发出的旧轮询/加载响应,不得在点击后落地并覆盖
 * 乐观写入的 compiling 状态;同纪元内乱序响应不得回退已应用的新状态。
 * 全部使用可控 Promise + fake timers,无真实等待,直接驱动真实页面行为。
 */
describe('Wiki 仪表盘过期快照防护 (E004-FIX-01)', () => {
    const compilingDoc = { id: 'doc_1', title: 'A', status: 'compiling' as const };
    const errorDoc = {
        id: 'doc_2',
        title: 'B',
        status: 'error' as const,
        error_code: 'timeout' as const,
    };
    const doc2Card = () => screen.getAllByTestId('wiki-card')[1];

    beforeEach(() => {
        mockedFetch.mockReset();
        mockedRecompile.mockReset();
        mockedDelete.mockReset();
        mockToastPush.mockReset();
    });

    afterEach(() => {
        jest.clearAllTimers();
        jest.useRealTimers();
    });

    test('stale pre-recompile poll response cannot overwrite the optimistic compiling state', async () => {
        jest.useFakeTimers();
        const stalePoll = createDeferred<WikiIndexData>();
        mockedFetch
            .mockResolvedValueOnce({
                total_docs: 2,
                documents: [compilingDoc, errorDoc],
            })
            .mockImplementationOnce(() => stalePoll.promise);
        mockedRecompile.mockResolvedValue({ status: 'recompiling' });

        render(<WikiPage />);
        const recompileBtn = await screen.findByTestId('recompile-btn');
        expect(screen.getByTestId('compiling-hint')).toBeInTheDocument();

        // 轮询请求 A 发出但保持未决(重编译点击前已在飞行中)
        await act(async () => {
            jest.advanceTimersByTime(3000);
            await flushMicrotasks();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(2);

        // 重编译成功:doc_2 乐观进入编译中,按钮消失
        fireEvent.click(recompileBtn);
        await waitFor(() => expect(screen.queryByTestId('recompile-btn')).toBeNull());
        expect(within(doc2Card()).getByTestId('status-badge')).toHaveTextContent('编译中');

        // 旧请求 A 此刻才带着上一轮的 error 状态落地:必须被丢弃
        await act(async () => {
            stalePoll.resolve({
                total_docs: 2,
                documents: [compilingDoc, errorDoc],
            });
            await flushMicrotasks();
        });

        // 页面仍显示本轮 compiling:提示条在、徽标不回退、重编译按钮不复活
        expect(screen.getByTestId('compiling-hint')).toBeInTheDocument();
        expect(within(doc2Card()).getByTestId('status-badge')).toHaveTextContent('编译中');
        expect(screen.queryByTestId('recompile-btn')).toBeNull();
    });

    test('after a stale discard, a new-epoch response still completes the round with one toast', async () => {
        jest.useFakeTimers();
        const stalePoll = createDeferred<WikiIndexData>();
        mockedFetch
            .mockResolvedValueOnce({
                total_docs: 2,
                documents: [compilingDoc, errorDoc],
            })
            .mockImplementationOnce(() => stalePoll.promise)
            .mockResolvedValueOnce({
                total_docs: 2,
                documents: [
                    { ...compilingDoc, status: 'compiled' as const },
                    { ...errorDoc, error_code: 'compile_failed' as const },
                ],
            });
        mockedRecompile.mockResolvedValue({ status: 'recompiling' });

        render(<WikiPage />);
        const recompileBtn = await screen.findByTestId('recompile-btn');

        // 轮询请求 A 在重编译点击前发出并保持未决
        await act(async () => {
            jest.advanceTimersByTime(3000);
            await flushMicrotasks();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(2);

        fireEvent.click(recompileBtn);
        await waitFor(() => expect(screen.queryByTestId('recompile-btn')).toBeNull());

        // 旧纪元响应被丢弃:不得产生 Toast、不得覆盖 compiling
        await act(async () => {
            stalePoll.resolve({
                total_docs: 2,
                documents: [compilingDoc, errorDoc],
            });
            await flushMicrotasks();
        });
        expect(mockToastPush).not.toHaveBeenCalled();
        expect(screen.getByTestId('compiling-hint')).toBeInTheDocument();

        // 新纪元轮询返回本轮真实终态:doc_2 编译失败
        await act(async () => {
            jest.advanceTimersByTime(3000);
            await flushMicrotasks();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(3);

        // 内联友好错误 + 每轮恰好一次 Toast,且无技术信息泄露
        await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(1));
        expect(mockToastPush).toHaveBeenCalledWith(
            '编译失败，请稍后重试或联系管理员',
            'error',
        );
        expect(
            within(doc2Card()).getByTestId('compile-error-message'),
        ).toHaveTextContent('编译失败，请稍后重试或联系管理员');
        expect(mockToastPush.mock.calls.flat().join(' ')).not.toMatch(
            /compile_failed|timeout|RuntimeError|OPENAI_API_KEY/,
        );
    });

    test('out-of-order responses within one epoch cannot regress an applied terminal state', async () => {
        // StrictMode 下挂载效应真实双跑 → 同纪元并发发出初始加载 A(先发)与 B(后发)
        const requestA = createDeferred<WikiIndexData>();
        const requestB = createDeferred<WikiIndexData>();
        mockedFetch
            .mockImplementationOnce(() => requestA.promise)
            .mockImplementationOnce(() => requestB.promise);

        render(
            <React.StrictMode>
                <WikiPage />
            </React.StrictMode>,
        );
        await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));

        // B(更新)先落地:终态 error 被应用;首轮不弹 Toast
        await act(async () => {
            requestB.resolve({
                total_docs: 1,
                documents: [{ id: 'doc_1', title: 'A', status: 'error', error_code: 'timeout' }],
            });
            await flushMicrotasks();
        });
        expect(screen.getByTestId('compile-error-message')).toHaveTextContent(
            '编译服务响应超时，请稍后重试',
        );
        expect(mockToastPush).not.toHaveBeenCalled();

        // A(更旧)后落地:不得把终态回退成 compiling
        await act(async () => {
            requestA.resolve({
                total_docs: 1,
                documents: [{ id: 'doc_1', title: 'A', status: 'compiling' }],
            });
            await flushMicrotasks();
        });
        expect(screen.getByTestId('compile-error-message')).toBeInTheDocument();
        expect(screen.getByTestId('status-badge')).toHaveTextContent('编译失败');
        expect(screen.queryByTestId('compiling-hint')).toBeNull();
        expect(mockToastPush).not.toHaveBeenCalled();
    });

    test('polling continues after a stale discard and stops only after the true terminal state', async () => {
        jest.useFakeTimers();
        const stalePoll = createDeferred<WikiIndexData>();
        mockedFetch
            .mockResolvedValueOnce({
                total_docs: 2,
                documents: [compilingDoc, errorDoc],
            })
            .mockImplementationOnce(() => stalePoll.promise)
            .mockResolvedValue({
                total_docs: 2,
                documents: [
                    { ...compilingDoc, status: 'compiled' as const },
                    { ...errorDoc, status: 'compiled' as const, error_code: undefined },
                ],
            });
        mockedRecompile.mockResolvedValue({ status: 'recompiling' });

        render(<WikiPage />);
        const recompileBtn = await screen.findByTestId('recompile-btn');

        // 轮询请求 A 在重编译点击前发出并保持未决
        await act(async () => {
            jest.advanceTimersByTime(3000);
            await flushMicrotasks();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(2);

        fireEvent.click(recompileBtn);
        await waitFor(() => expect(screen.queryByTestId('recompile-btn')).toBeNull());

        // 旧响应把两份文档都描述为终态:若被错误应用,hasPending 变 false,轮询将永久停止
        await act(async () => {
            stalePoll.resolve({
                total_docs: 2,
                documents: [
                    { ...compilingDoc, status: 'compiled' as const },
                    errorDoc,
                ],
            });
            await flushMicrotasks();
        });
        // 丢弃后本轮仍在编译:提示条必须在
        expect(screen.getByTestId('compiling-hint')).toBeInTheDocument();

        // 轮询在过期丢弃后继续:新请求发出并带回本轮真实终态
        await act(async () => {
            jest.advanceTimersByTime(3000);
            await flushMicrotasks();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(3);
        await waitFor(() => expect(screen.queryByTestId('compiling-hint')).toBeNull());

        // 全部终态后:再推进 6000ms 不应再有新请求
        await act(async () => {
            jest.advanceTimersByTime(6000);
            await flushMicrotasks();
        });
        expect(mockedFetch).toHaveBeenCalledTimes(3);
    });
});
