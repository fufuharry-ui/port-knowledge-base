/**
 * tests/unit/SearchResult.test.tsx — SearchResult 组件 TDD 测试
 * 验证答案展示、引用来源徽章、加载态、流式文本追加
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen } from '@testing-library/react';
import SearchResult from '@/components/SearchResult';

const mockSources = [
    { doc_id: 'doc_001', title: '岸桥远控技术方案' },
    { doc_id: 'doc_002', title: '5G专网部署规范' },
];

describe('SearchResult', () => {
    test('renders answer text', () => {
        render(
            <SearchResult answer="岸桥端到端延迟≤50ms" sources={[]} isLoading={false} />
        );
        expect(screen.getByTestId('answer-text')).toHaveTextContent('岸桥端到端延迟≤50ms');
    });

    test('renders source badges for each source', () => {
        render(
            <SearchResult answer="测试答案" sources={mockSources} isLoading={false} />
        );
        const badges = screen.getAllByTestId(/^source-badge-/);
        expect(badges).toHaveLength(2);
    });

    test('source badge contains doc_id', () => {
        render(
            <SearchResult answer="测试答案" sources={mockSources} isLoading={false} />
        );
        expect(screen.getByTestId('source-badge-doc_001')).toBeInTheDocument();
        expect(screen.getByTestId('source-badge-doc_001')).toHaveTextContent('doc_001');
    });

    test('source badge shows title', () => {
        render(
            <SearchResult answer="测试答案" sources={mockSources} isLoading={false} />
        );
        expect(screen.getByTestId('source-badge-doc_001')).toHaveTextContent('岸桥远控技术方案');
    });

    test('shows spinner when isLoading is true', () => {
        render(
            <SearchResult answer="" sources={[]} isLoading={true} />
        );
        expect(screen.getByTestId('answer-spinner')).toBeInTheDocument();
    });

    test('does not show spinner when not loading', () => {
        render(
            <SearchResult answer="完整答案" sources={[]} isLoading={false} />
        );
        expect(screen.queryByTestId('answer-spinner')).not.toBeInTheDocument();
    });

    test('shows empty state when no answer and not loading', () => {
        render(
            <SearchResult answer="" sources={[]} isLoading={false} />
        );
        expect(screen.getByTestId('answer-empty')).toBeInTheDocument();
    });

    test('renders streaming text correctly as it accumulates', () => {
        const { rerender } = render(
            <SearchResult answer="岸桥" sources={[]} isLoading={true} />
        );
        expect(screen.getByTestId('answer-text')).toHaveTextContent('岸桥');

        rerender(
            <SearchResult answer="岸桥远控延迟≤50ms" sources={[]} isLoading={false} />
        );
        expect(screen.getByTestId('answer-text')).toHaveTextContent('岸桥远控延迟≤50ms');
    });
});
