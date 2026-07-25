'use client';
import React from 'react';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import * as Tooltip from '@radix-ui/react-tooltip';
import { FileText } from 'lucide-react';
import type { CitationMeta } from '@/lib/qa-stream';
import { cn } from '@/lib/utils';

interface ChatBubbleProps {
    role: 'user' | 'assistant';
    content: string;
    citations: CitationMeta[];
}

/**
 * linkifyCitations — 把正文中的引用标记改写为 markdown 链接,交给 react-markdown
 * 渲染,再由 components.a 拦截 `#cite-<doc_id>` 还原为可交互的引用 Tooltip。
 * 支持两种引用格式(与 Big-Loop #1 修复一致,逻辑未变,仅输出形式改为链接):
 *  1) [N] 数字引用(后端 citations 的 ref,如 "[1]")
 *  2) [标题 · 章节] 引用 — layer3 prompt(scripts/search.py,冻结)约定的格式。
 * 链接 label 保留原始标记文本(如 "[1]"),利用 CommonMark 平衡括号规则:
 * "[[1]](#cite-doc_001)" 的 label 即 "[1]"。
 */
export function linkifyCitations(content: string, citations: CitationMeta[]): string {
    const byRef = new Map(citations.map(c => [c.ref, c]));
    const byTitle = new Map(citations.filter(c => c.title).map(c => [c.title!, c] as const));
    const parts = content.split(/(\[[^\]]+?\s*·\s*[^\]]+\]|\[\d+\])/);
    return parts.map(part => {
        let meta: CitationMeta | undefined = byRef.get(part);
        if (!meta) {
            const m = part.match(/^\[([^\]]+?)\s*·\s*[^\]]+\]$/);
            if (m) meta = byTitle.get(m[1].trim()) ?? byRef.get(m[0]);
        }
        if (meta) {
            // label 保留原标记(含外层方括号 → 双层括号,CommonMark 平衡括号合法)
            return `[${part}](#cite-${meta.doc_id})`;
        }
        return part;
    }).join('');
}

/** 引用标记:sup 触发器 + 浅色 Tooltip(来源标题/章节/doc_id + 穿梭链接) */
function CitationTrigger({ meta, refText }: { meta: CitationMeta; refText: string }) {
    return (
        <Tooltip.Root>
            <Tooltip.Trigger asChild>
                <sup
                    data-testid="citation-trigger"
                    className="ml-0.5 inline-block cursor-pointer select-none rounded-sm border border-accent/25 bg-accent-soft px-1 py-px align-super text-[11px] font-semibold leading-4 text-accent-ink transition-colors hover:bg-accent/15"
                >
                    {refText}
                </sup>
            </Tooltip.Trigger>
            <Tooltip.Portal>
                <Tooltip.Content
                    side="top"
                    sideOffset={6}
                    className="z-[9999] max-w-[300px] rounded-lg border border-line bg-surface px-3.5 py-2.5 shadow-lg"
                >
                    <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
                        <FileText size={12} className="shrink-0 text-accent" />
                        {meta.title ?? meta.doc_id}
                    </div>
                    {meta.section && (
                        <div className="mt-0.5 text-[11px] text-ink-3">§ {meta.section}</div>
                    )}
                    <div className="mt-1 font-mono text-[10px] text-ink-3">{meta.doc_id}</div>
                    <Link
                        href={`/wiki/${meta.doc_id}`}
                        data-testid={`citation-link-${meta.doc_id}`}
                        className="mt-2 block border-t border-line pt-1.5 text-[11px] font-medium text-accent no-underline hover:underline"
                    >
                        查看文档详情 →
                    </Link>
                    <Tooltip.Arrow className="fill-line" />
                </Tooltip.Content>
            </Tooltip.Portal>
        </Tooltip.Root>
    );
}

export default function ChatBubble({ role, content, citations }: ChatBubbleProps) {
    const isUser = role === 'user';
    const byId = new Map(citations.map(c => [c.doc_id, c]));

    return (
        <div
            data-testid="chat-bubble"
            data-role={role}
            className={cn('mb-4 flex flex-col fade-in-up', isUser ? 'items-end' : 'items-start')}
        >
            {/* Role label */}
            <span className="mb-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-3">
                {isUser ? '你' : '智能知识库'}
            </span>

            {/* Bubble */}
            <div
                className={cn(
                    'max-w-[85%] rounded-xl px-4 py-3 text-sm leading-7',
                    isUser
                        ? 'rounded-br-sm bg-accent text-white shadow-xs'
                        : 'rounded-bl-sm border border-line bg-surface text-ink shadow-xs',
                )}
            >
                {isUser ? (
                    <span className="whitespace-pre-wrap">{content}</span>
                ) : (
                    <Tooltip.Provider delayDuration={200}>
                        <div className="md-body">
                            <ReactMarkdown
                                components={{
                                    a: ({ href, children }) => {
                                        const m = href?.match(/^#cite-(.+)$/);
                                        const meta = m ? byId.get(m[1]) : undefined;
                                        if (meta) {
                                            return (
                                                <CitationTrigger
                                                    meta={meta}
                                                    refText={typeof children === 'string' ? children : String(children ?? '')}
                                                />
                                            );
                                        }
                                        return (
                                            <a href={href} target="_blank" rel="noreferrer">
                                                {children}
                                            </a>
                                        );
                                    },
                                }}
                            >
                                {linkifyCitations(content, citations)}
                            </ReactMarkdown>
                        </div>
                    </Tooltip.Provider>
                )}
            </div>
        </div>
    );
}
