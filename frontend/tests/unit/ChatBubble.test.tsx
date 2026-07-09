/**
 * tests/unit/ChatBubble.test.tsx — ChatBubble 引用 Tooltip TDD 测试 (Phase E)
 * 验证 [1] 标记解析、Tooltip 触发器渲染、纯文本段落渲染
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen } from '@testing-library/react';
import ChatBubble from '@/components/ChatBubble';
import type { CitationMeta } from '@/lib/qa-stream';

const mockCitations: CitationMeta[] = [
    { ref: '[1]', doc_id: 'doc_001', title: '岸桥远控技术方案', section: '第3章 网络方案' },
    { ref: '[2]', doc_id: 'doc_002', title: '5G港口网络规划', section: '第7页 表2' },
];

describe('ChatBubble', () => {
    test('renders plain text without citation markers', () => {
        render(
            <ChatBubble
                role="assistant"
                content="这是一段没有引用标记的普通文本。"
                citations={[]}
            />
        );
        expect(screen.getByText(/这是一段没有引用标记的普通文本/)).toBeInTheDocument();
    });

    test('renders user message with user role styling', () => {
        const { container } = render(
            <ChatBubble role="user" content="我的问题" citations={[]} />
        );
        // user 消息应有对应的 data-role 属性
        expect(container.querySelector('[data-role="user"]')).toBeInTheDocument();
    });

    test('renders assistant message with assistant role styling', () => {
        const { container } = render(
            <ChatBubble role="assistant" content="助手回答" citations={[]} />
        );
        expect(container.querySelector('[data-role="assistant"]')).toBeInTheDocument();
    });

    test('renders citation markers [1] as tooltip triggers', () => {
        render(
            <ChatBubble
                role="assistant"
                content="延迟要求≤50ms[1]，5G空口延迟≤20ms[2]。"
                citations={mockCitations}
            />
        );
        // 两个引用标记应渲染为 tooltip trigger 元素
        const triggers = screen.getAllByTestId('citation-trigger');
        expect(triggers).toHaveLength(2);
        expect(triggers[0]).toHaveTextContent('[1]');
        expect(triggers[1]).toHaveTextContent('[2]');
    });

    test('citation triggers show ref text', () => {
        render(
            <ChatBubble
                role="assistant"
                content="参考资料[1]。"
                citations={mockCitations}
            />
        );
        const trigger = screen.getByTestId('citation-trigger');
        expect(trigger).toHaveTextContent('[1]');
    });

    test('renders text without breaking when citation index is out of range', () => {
        // content 中有 [3] 但 citations 只有 [1] [2]
        render(
            <ChatBubble
                role="assistant"
                content="参考[3]未知引用。"
                citations={mockCitations}
            />
        );
        // 不应崩溃，[3] 应作为普通文本或无效标记渲染
        expect(screen.getByTestId('chat-bubble')).toBeInTheDocument();
    });

    test('renders multiple text segments between citations', () => {
        render(
            <ChatBubble
                role="assistant"
                content="开头文本[1]中间文本[2]结尾文本。"
                citations={mockCitations}
            />
        );
        // 文本内容整体应包含全部片段
        const bubble = screen.getByTestId('chat-bubble');
        expect(bubble.textContent).toContain('开头文本');
        expect(bubble.textContent).toContain('中间文本');
        expect(bubble.textContent).toContain('结尾文本');
    });
});
