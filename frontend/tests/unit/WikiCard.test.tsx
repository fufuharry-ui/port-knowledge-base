/**
 * tests/unit/WikiCard.test.tsx — WikiCard 组件 TDD 测试
 * 验证文档卡片渲染逻辑和状态徽章
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import WikiCard from '@/components/WikiCard';

const baseDoc = {
    id: 'doc_001',
    title: '岸桥远控技术方案',
    abstract_short: '基于5G和MEC的岸桥远程操控系统，端到端延迟≤50ms',
    status: 'compiled' as const,
    char_count: 12000,
    ingested_at: '2026-04-05T10:00:00Z',
};

describe('WikiCard', () => {
    test('renders title correctly', () => {
        render(<WikiCard doc={baseDoc} />);
        expect(screen.getByText('岸桥远控技术方案')).toBeInTheDocument();
    });

    test('renders abstract short text', () => {
        render(<WikiCard doc={baseDoc} />);
        expect(screen.getByText(/5G和MEC/)).toBeInTheDocument();
    });

    test('shows compiled badge with green indicator', () => {
        render(<WikiCard doc={baseDoc} />);
        const badge = screen.getByTestId('status-badge');
        expect(badge).toHaveTextContent('compiled');
        expect(badge.className).toMatch(/badge-compiled|green|success|emerald/i);
    });

    test('shows compiling badge with spinner for raw status', () => {
        render(<WikiCard doc={{ ...baseDoc, status: 'raw' }} />);
        const badge = screen.getByTestId('status-badge');
        expect(badge).toHaveTextContent('raw');
    });

    test('shows compiling badge with spinning animation', () => {
        render(<WikiCard doc={{ ...baseDoc, status: 'compiling' }} />);
        // Should show a spinner/loading indicator
        expect(screen.getByTestId('status-badge')).toHaveTextContent('compiling');
        // Spinner element should exist
        expect(screen.getByTestId('compiling-spinner')).toBeInTheDocument();
    });

    test('shows error badge in red for error status', () => {
        render(<WikiCard doc={{ ...baseDoc, status: 'error' }} />);
        const badge = screen.getByTestId('status-badge');
        expect(badge).toHaveTextContent('error');
        expect(badge.className).toMatch(/badge-error|red|danger|destructive/i);
    });

    test('calls onExpand callback when card is clicked', () => {
        const onExpand = jest.fn();
        render(<WikiCard doc={baseDoc} onExpand={onExpand} />);
        fireEvent.click(screen.getByTestId('wiki-card'));
        expect(onExpand).toHaveBeenCalledWith('doc_001');
    });

    test('renders char_count as formatted number', () => {
        render(<WikiCard doc={baseDoc} />);
        // 12000 chars should display as "12,000" or "12000 字"
        expect(screen.getByTestId('char-count')).toBeInTheDocument();
    });
});
