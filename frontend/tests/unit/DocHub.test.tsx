/**
 * tests/unit/DocHub.test.tsx — 文档智能枢纽组件 TDD 测试 (Loop #7)
 * 验证一体化文档视图:摘要 + 实体chip(可点到实体页)+ 关联文档(可点到文档页)+ 矛盾
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen } from '@testing-library/react';
import DocHub from '@/components/DocHub';
import type { DocMeta } from '@/lib/api';

const doc: DocMeta = {
    id: 'doc_20260405_001',
    title: '岸桥远控方案',
    abstract_short: '本方案阐述岸桥远控的网络延迟要求与架构。',
    ontology_terms: ['岸桥远控', '5G专网', '端到端延迟'],
    status: 'compiled',
    char_count: 12000,
};

const relatedDocs = [
    { doc_id: 'doc_20260405_003', title: '5G白皮书', type: 'same_topic', confidence: 0.9 },
];

const contradictions = [
    { doc_a: 'doc_20260405_001', doc_b: 'doc_20260405_003',
      conflict_point: '延迟要求', reasoning_chain: 'A说50ms,B说20ms', confidence: 0.85 },
];

describe('DocHub 文档智能枢纽', () => {
    test('渲染文档标题 + 摘要 + 元数据', () => {
        render(<DocHub doc={doc} relatedDocs={[]} contradictions={[]} />);
        expect(screen.getByText('岸桥远控方案')).toBeInTheDocument();
        expect(screen.getByText(/网络延迟要求与架构/)).toBeInTheDocument();
        expect(screen.getByText(/compiled/)).toBeInTheDocument();
    });

    test('实体 chip 可点击,链接到实体图谱页并预填 term', () => {
        const { container } = render(<DocHub doc={doc} relatedDocs={[]} contradictions={[]} />);
        // 每个 ontology_term 应有指向 /entity-graph?term=X 的链接
        const entityLinks = container.querySelectorAll(`a[href*="/entity-graph?term="]`);
        expect(entityLinks.length).toBeGreaterThanOrEqual(3);
        const hrefs = Array.from(entityLinks).map(a => decodeURIComponent(a.getAttribute('href') || ''));
        expect(hrefs.some(h => h.includes('岸桥远控'))).toBe(true);
        expect(hrefs.some(h => h.includes('5G专网'))).toBe(true);
    });

    test('关联文档可点击,链接到 /wiki/[id],展示关系类型', () => {
        const { container } = render(<DocHub doc={doc} relatedDocs={relatedDocs} contradictions={[]} />);
        const link = container.querySelector(`a[href="/wiki/doc_20260405_003"]`);
        expect(link).toBeInTheDocument();
        expect(link?.textContent).toContain('5G白皮书');
        expect(link?.textContent).toContain('同类主题'); // 关系类型中文标签
    });

    test('矛盾面板:有矛盾时展示冲突点 + 推理链', () => {
        render(<DocHub doc={doc} relatedDocs={[]} contradictions={contradictions} />);
        // 冲突点(矛盾面板内)
        expect(screen.getAllByText(/延迟要求/).length).toBeGreaterThan(0);
        expect(screen.getByText(/A说50ms.*B说20ms/)).toBeInTheDocument();
        // 矛盾涉及的对方文档可点击
        const otherLink = screen.getAllByText(/doc_20260405_003/);
        expect(otherLink.length).toBeGreaterThan(0);
    });

    test('无关联文档时诚实展示空态(不留白误导)', () => {
        render(<DocHub doc={doc} relatedDocs={[]} contradictions={[]} />);
        expect(screen.getByText(/暂无关联文档|无关联/)).toBeInTheDocument();
    });

    test('返回知识库的导航', () => {
        const { container } = render(<DocHub doc={doc} relatedDocs={[]} contradictions={[]} />);
        expect(container.querySelector('a[href="/wiki"]')).toBeInTheDocument();
    });
});
