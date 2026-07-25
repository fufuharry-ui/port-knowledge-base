/**
 * __mocks__/reactMarkdown.tsx — Jest 用 react-markdown mock
 * react-markdown v10 是纯 ESM,jest(CJS/jsdom)无法直接加载,故以本 mock 替代。
 * 只做两件事:
 *  1) 原文渲染文本(不解析 markdown 语法 — 单元测试不验证 markdown 渲染,真实渲染由 build/E2E 覆盖);
 *  2) 解析 [label](href) 链接并调用 components.a — 驱动 ChatBubble 的引用 Tooltip 单元测试。
 *    支持 ChatBubble.linkifyCitations 产出的平衡括号形式 "[[1]](#cite-doc_001)"。
 */
import React from 'react';

interface MockReactMarkdownProps {
    children?: React.ReactNode;
    components?: {
        a?: React.ComponentType<{ href?: string; children?: React.ReactNode }>;
        [key: string]: unknown;
    };
}

export default function MockReactMarkdown({ children, components }: MockReactMarkdownProps) {
    const text = String(children ?? '');
    const A = components?.a;
    const nodes: React.ReactNode[] = [];
    // 先匹配平衡括号形式 [[label]](href),再匹配普通 [label](href)
    const re = /\[\[([^\]]+)\]\]\(([^)\s]+)\)|\[([^\]]+)\]\(([^)\s]+)\)/g;
    let last = 0;
    let m: RegExpExecArray | null;
    let i = 0;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) nodes.push(text.slice(last, m.index));
        const label = m[1] !== undefined ? `[${m[1]}]` : m[3];
        const href = m[2] ?? m[4];
        nodes.push(
            A
                ? React.createElement(A, { key: `md-a-${i++}`, href }, label)
                : React.createElement('a', { key: `md-a-${i++}`, href }, label),
        );
        last = m.index + m[0].length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return React.createElement('div', { 'data-testid': 'markdown' }, ...nodes);
}
