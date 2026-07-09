'use client';
import React from 'react';
import Link from 'next/link';
import * as Tooltip from '@radix-ui/react-tooltip';
import type { CitationMeta } from '@/lib/qa-stream';

interface ChatBubbleProps {
    role: 'user' | 'assistant';
    content: string;
    citations: CitationMeta[];
}

/**
 * 将内容按 [N] 引用标记切分为「文本段」和「引用标记」交替数组
 */
function parseContentWithCitations(
    content: string,
    citations: CitationMeta[],
): Array<{ type: 'text'; value: string } | { type: 'citation'; meta: CitationMeta; ref: string }> {
    const citationMap = new Map(citations.map(c => [c.ref, c]));
    // 匹配 [1] [2] ... [99]
    const parts = content.split(/(\[\d+\])/);
    return parts.map(part => {
        if (citationMap.has(part)) {
            return { type: 'citation' as const, meta: citationMap.get(part)!, ref: part };
        }
        return { type: 'text' as const, value: part };
    });
}

export default function ChatBubble({ role, content, citations }: ChatBubbleProps) {
    const isUser = role === 'user';
    const segments = parseContentWithCitations(content, citations);

    return (
        <div
            data-testid="chat-bubble"
            data-role={role}
            style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: isUser ? 'flex-end' : 'flex-start',
                marginBottom: '16px',
            }}
        >
            {/* Role label */}
            <span style={{
                fontSize: '10px',
                color: 'var(--text-muted)',
                marginBottom: '4px',
                fontFamily: 'var(--font-mono, monospace)',
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
            }}>
                {isUser ? '你' : 'PortGPT'}
            </span>

            {/* Bubble */}
            <div style={{
                maxWidth: '82%',
                padding: '12px 16px',
                borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
                background: isUser
                    ? 'linear-gradient(135deg, rgba(59,130,246,0.25), rgba(139,92,246,0.20))'
                    : 'rgba(255,255,255,0.04)',
                border: `1px solid ${isUser
                    ? 'rgba(59,130,246,0.30)'
                    : 'rgba(255,255,255,0.08)'}`,
                fontSize: '14px',
                lineHeight: '1.7',
                color: 'var(--text-primary)',
                backdropFilter: 'blur(8px)',
                transition: 'all 0.2s ease',
            }}>
                <Tooltip.Provider delayDuration={200}>
                    {segments.map((seg, i) => {
                        if (seg.type === 'text') {
                            return (
                                <span key={`text-${i}`} style={{ whiteSpace: 'pre-wrap' }}>
                                    {seg.value}
                                </span>
                            );
                        }
                        // Citation marker with tooltip
                        return (
                            <Tooltip.Root key={`citation-${i}`}>
                                <Tooltip.Trigger asChild>
                                    <sup
                                        data-testid="citation-trigger"
                                        style={{
                                            display: 'inline-block',
                                            padding: '0 4px',
                                            marginLeft: '1px',
                                            fontSize: '11px',
                                            fontWeight: 600,
                                            color: '#60a5fa',
                                            background: 'rgba(59,130,246,0.15)',
                                            borderRadius: '4px',
                                            border: '1px solid rgba(59,130,246,0.25)',
                                            cursor: 'pointer',
                                            userSelect: 'none',
                                            transition: 'background 0.15s',
                                        }}
                                    >
                                        {seg.ref}
                                    </sup>
                                </Tooltip.Trigger>
                                <Tooltip.Portal>
                                    <Tooltip.Content
                                        side="top"
                                        sideOffset={6}
                                        style={{
                                            background: 'rgba(10,10,18,0.96)',
                                            border: '1px solid rgba(255,255,255,0.12)',
                                            borderRadius: '10px',
                                            padding: '10px 14px',
                                            maxWidth: '280px',
                                            backdropFilter: 'blur(16px)',
                                            boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
                                            zIndex: 9999,
                                        }}
                                    >
                                        <div style={{
                                            fontSize: '12px',
                                            fontWeight: 600,
                                            color: '#93c5fd',
                                            marginBottom: '4px',
                                        }}>
                                            📄 {seg.meta.title ?? seg.meta.doc_id}
                                        </div>
                                        {seg.meta.section && (
                                            <div style={{
                                                fontSize: '11px',
                                                color: 'var(--text-muted)',
                                            }}>
                                                § {seg.meta.section}
                                            </div>
                                        )}
                                        <div style={{
                                            fontSize: '10px',
                                            color: 'rgba(255,255,255,0.3)',
                                            marginTop: '4px',
                                            fontFamily: 'monospace',
                                        }}>
                                            {seg.meta.doc_id}
                                        </div>
                                        <Link
                                            href={`/wiki/${seg.meta.doc_id}`}
                                            data-testid={`citation-link-${seg.meta.doc_id}`}
                                            style={{
                                                display: 'block',
                                                marginTop: '8px',
                                                fontSize: '11px',
                                                color: '#93c5fd',
                                                textDecoration: 'none',
                                                borderTop: '1px solid rgba(255,255,255,0.08)',
                                                paddingTop: '6px',
                                            }}
                                        >
                                            查看文档详情 →
                                        </Link>
                                        <Tooltip.Arrow style={{ fill: 'rgba(255,255,255,0.12)' }} />
                                    </Tooltip.Content>
                                </Tooltip.Portal>
                            </Tooltip.Root>
                        );
                    })}
                </Tooltip.Provider>
            </div>
        </div>
    );
}
