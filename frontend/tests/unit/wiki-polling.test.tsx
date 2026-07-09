/**
 * tests/unit/wiki-polling.test.tsx — 仪表盘编译轮询测试 (Loop #11)
 * 验证:有 raw/compiling 文档时显示"编译中"提示;全 compiled 时无提示。
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import WikiPage from '@/app/wiki/page';
import { fetchWikiIndex } from '@/lib/api';

jest.mock('@/lib/api', () => ({
    fetchWikiIndex: jest.fn(),
    deleteDoc: jest.fn(),
    recompileDoc: jest.fn(),
}));

const mockedFetch = fetchWikiIndex as jest.MockedFunction<typeof fetchWikiIndex>;

describe('Wiki polling (Loop #11)', () => {
    beforeEach(() => { mockedFetch.mockReset(); });

    test('shows compiling hint when raw docs exist', async () => {
        mockedFetch.mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', title: 'T', status: 'raw' }] as any,
        });
        await act(async () => { render(<WikiPage />); });
        expect(await screen.findByTestId('compiling-hint')).toBeInTheDocument();
    });

    test('no compiling hint when all compiled', async () => {
        mockedFetch.mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', title: 'T', status: 'compiled' }] as any,
        });
        await act(async () => { render(<WikiPage />); });
        await screen.findByTestId('doc-count');
        expect(screen.queryByTestId('compiling-hint')).toBeNull();
    });
});
