/**
 * tests/unit/OntologyTree.test.tsx — 本体树递归渲染测试 (Big-Loop #4)
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import OntologyTree from '@/components/OntologyTree';
import type { OntologyNode } from '@/lib/api';

const TREE: OntologyNode[] = [
    {
        term: '智慧港口', parent: null, definition: '根概念',
        children: [
            { term: '港口自动化', parent: '智慧港口', definition: '自动化作业', children: [] },
            {
                term: '通信技术', parent: '智慧港口', definition: '网络通信',
                children: [
                    { term: '5G专网', parent: '通信技术', children: [] },
                ],
            },
        ],
    },
];

describe('OntologyTree', () => {
    test('renders root nodes and definitions', () => {
        render(<OntologyTree nodes={TREE} />);
        expect(screen.getByText('智慧港口')).toBeInTheDocument();
        expect(screen.getByText('根概念')).toBeInTheDocument();
        // 子节点(顶层默认展开)也可见
        expect(screen.getByText('港口自动化')).toBeInTheDocument();
    });

    test('collapses and expands on click', () => {
        render(<OntologyTree nodes={TREE} />);
        // 通信技术 是 level-1 节点,默认折叠 → 孙节点 5G专网 初始不可见
        expect(screen.queryByText('5G专网')).not.toBeInTheDocument();

        // 点击 通信技术 展开 → 5G专网 出现
        fireEvent.click(screen.getByText('通信技术'));
        expect(screen.getByText('5G专网')).toBeInTheDocument();

        // 再点击 → 折叠 → 5G专网 消失
        fireEvent.click(screen.getByText('通信技术'));
        expect(screen.queryByText('5G专网')).not.toBeInTheDocument();
    });

    test('shows empty state when no nodes', () => {
        const { container } = render(<OntologyTree nodes={[]} />);
        expect(container.querySelector('[data-role="ontology-empty"]')).toBeInTheDocument();
    });
});
