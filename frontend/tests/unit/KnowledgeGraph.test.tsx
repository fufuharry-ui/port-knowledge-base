/**
 * tests/unit/KnowledgeGraph.test.tsx — KnowledgeGraph 组件 TDD 测试
 * 验证 ECharts 图谱渲染、空状态、节点数量
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen } from '@testing-library/react';
import KnowledgeGraph from '@/components/KnowledgeGraph';

const mockNodes = [
    { id: 'doc_001', title: '岸桥远控技术方案' },
    { id: 'doc_002', title: '5G专网部署规范' },
    { id: 'doc_003', title: 'MEC边缘计算方案' },
];

const mockEdges = [
    { source: 'doc_001', target: 'doc_002', type: 'supplements', confidence: 0.9 },
    { source: 'doc_001', target: 'doc_003', type: 'same_topic', confidence: 0.75 },
];

describe('KnowledgeGraph', () => {
    test('renders without crashing with valid nodes and edges', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByTestId('knowledge-graph')).toBeInTheDocument();
    });

    test('renders empty state message when no nodes', () => {
        render(<KnowledgeGraph nodes={[]} edges={[]} />);
        expect(screen.getByTestId('graph-empty')).toBeInTheDocument();
        expect(screen.getByTestId('graph-empty')).toHaveTextContent(/暂无|无数据|empty/i);
    });

    test('passes correct node count to chart option', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} />);
        // The mock ECharts records data-nodes attribute
        const chart = screen.getByTestId('echarts-mock');
        expect(Number(chart.getAttribute('data-nodes'))).toBe(3);
    });

    test('renders graph container with correct data-testid', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} />);
        const container = screen.getByTestId('knowledge-graph');
        expect(container).toBeInTheDocument();
    });

    test('handles single node without edges', () => {
        render(<KnowledgeGraph nodes={[mockNodes[0]]} edges={[]} />);
        expect(screen.getByTestId('knowledge-graph')).toBeInTheDocument();
        expect(screen.queryByTestId('graph-empty')).not.toBeInTheDocument();
    });

    test('shows node count stat', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByTestId('node-count')).toHaveTextContent('3');
    });

    test('shows edge count stat', () => {
        render(<KnowledgeGraph nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByTestId('edge-count')).toHaveTextContent('2');
    });
});
