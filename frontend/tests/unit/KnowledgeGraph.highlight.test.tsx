/**
 * tests/unit/KnowledgeGraph.highlight.test.tsx
 * 验证 KnowledgeGraph 接收 highlightIds prop 时正确高亮节点
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen } from '@testing-library/react';
import KnowledgeGraph from '@/components/KnowledgeGraph';

const mockNodes = [
    { id: 'doc_001', title: '岸桥远控技术方案' },
    { id: 'doc_002', title: '5G港口网络规划' },
    { id: 'doc_003', title: 'MEC边缘计算方案' },
];
const mockEdges = [
    { source: 'doc_001', target: 'doc_002', type: 'supplements', confidence: 0.9 },
];

describe('KnowledgeGraph — highlightIds prop', () => {
    test('renders without highlightIds prop (backward compat)', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByTestId('knowledge-graph')).toBeInTheDocument();
    });

    test('renders with empty highlightIds array without error', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} highlightIds={[]} />);
        expect(screen.getByTestId('knowledge-graph')).toBeInTheDocument();
    });

    test('renders with valid highlightIds without crashing', () => {
        render(
            <KnowledgeGraph
                nodes={mockNodes}
                edges={mockEdges}
                highlightIds={['doc_001', 'doc_003']}
            />
        );
        expect(screen.getByTestId('knowledge-graph')).toBeInTheDocument();
    });

    test('echarts receives data-highlight-count equal to matched highlight nodes', () => {
        render(
            <KnowledgeGraph
                nodes={mockNodes}
                edges={mockEdges}
                highlightIds={['doc_001', 'doc_002']}
            />
        );
        // The ECharts mock stores highlight count in data-highlight-count
        const chart = screen.getByTestId('echarts-mock');
        expect(Number(chart.getAttribute('data-highlight-count'))).toBe(2);
    });

    test('echarts receives data-highlight-count 0 when no ids match', () => {
        render(
            <KnowledgeGraph
                nodes={mockNodes}
                edges={mockEdges}
                highlightIds={['doc_999']}
            />
        );
        const chart = screen.getByTestId('echarts-mock');
        expect(Number(chart.getAttribute('data-highlight-count'))).toBe(0);
    });
});
