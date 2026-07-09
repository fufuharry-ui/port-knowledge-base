'use client';
import React from 'react';
import Link from 'next/link';
import type { SearchSource } from '@/lib/api';

interface SearchResultProps {
    answer: string;
    sources: SearchSource[];
    isLoading: boolean;
}

export default function SearchResult({ answer, sources, isLoading }: SearchResultProps) {
    if (!isLoading && !answer) {
        return (
            <div data-testid="answer-empty" style={{
                textAlign: 'center', color: 'var(--text-muted)',
                padding: '48px 0', fontSize: '14px', lineHeight: 1.7,
            }}>
                在上方输入您的问题，我将从知识库中检索相关文档并给出专业回答。
                <br />
                <span style={{ fontSize: '12px', marginTop: '8px', display: 'block', color: 'var(--text-muted)' }}>
                    支持中文自然语言提问 · 三层渐进式检索 · SSE 流式输出
                </span>
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            {/* Answer block */}
            <div className="answer-block" style={{ padding: '24px', position: 'relative' }}>
                {isLoading && !answer && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--accent-blue)' }}>
                        <span data-testid="answer-spinner" className="spin" style={{
                            display: 'inline-block', width: '14px', height: '14px',
                            border: '2px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                        }} />
                        <span style={{ fontSize: '14px' }}>正在检索知识库...</span>
                    </div>
                )}

                {answer && (
                    <>
                        {isLoading && (
                            <span data-testid="answer-spinner" className="spin" style={{
                                position: 'absolute', top: '14px', right: '14px',
                                display: 'inline-block', width: '10px', height: '10px',
                                border: '1.5px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                            }} />
                        )}
                        <div
                            data-testid="answer-text"
                            style={{
                                fontSize: '14px', lineHeight: 1.8,
                                color: 'var(--text-primary)',
                                whiteSpace: 'pre-wrap',
                            }}
                        >
                            {answer}
                            {isLoading && (
                                <span className="cursor-blink" style={{
                                    display: 'inline-block', width: '2px', height: '16px',
                                    background: 'var(--accent-blue)', marginLeft: '2px', verticalAlign: 'middle',
                                }} />
                            )}
                        </div>
                    </>
                )}
            </div>

            {/* Sources */}
            {sources.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <span>📎</span> 引用来源
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                        {sources.map(src => (
                            <Link
                                key={src.doc_id}
                                href={`/wiki/${src.doc_id}`}
                                data-testid={`source-badge-${src.doc_id}`}
                                className="source-badge"
                                style={{ textDecoration: 'none', cursor: 'pointer' }}
                                title={`查看《${src.title ?? src.doc_id}》文档详情`}
                            >
                                <span style={{ fontFamily: 'monospace', fontSize: '10px', color: '#60a5fa' }}>{src.doc_id}</span>
                                {src.title && (
                                    <>
                                        <span style={{ color: 'var(--text-muted)' }}>·</span>
                                        <span style={{ maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {src.title}
                                        </span>
                                    </>
                                )}
                            </Link>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
