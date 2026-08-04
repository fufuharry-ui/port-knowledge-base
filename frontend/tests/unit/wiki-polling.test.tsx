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
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import WikiPage from '@/app/wiki/page';
import { fetchWikiIndex, recompileDoc } from '@/lib/api';

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

describe('Wiki 仪表盘编译轮询与失败 Toast (E004 Task 8)', () => {
    beforeEach(() => {
        mockedFetch.mockReset();
        mockedRecompile.mockReset();
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
});
