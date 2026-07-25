'use client';
import React from 'react';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import { Paperclip } from 'lucide-react';
import type { SearchSource } from '@/lib/api';
import { Spinner } from '@/components/ui/Spinner';

interface SearchResultProps {
    answer: string;
    sources: SearchSource[];
    isLoading: boolean;
}

export default function SearchResult({ answer, sources, isLoading }: SearchResultProps) {
    if (!isLoading && !answer) {
        return (
            <div
                data-testid="answer-empty"
                className="rounded-xl border border-dashed border-line-strong bg-surface px-6 py-12 text-center"
            >
                <p className="text-sm leading-7 text-ink-2">
                    在上方输入您的问题,我将从知识库中检索相关文档并给出专业回答。
                </p>
                <p className="mt-2 text-xs text-ink-3">
                    支持中文自然语言提问 · 三层渐进式检索 · SSE 流式输出
                </p>
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-5">
            {/* Answer block */}
            <div className="answer-block relative p-6">
                {isLoading && !answer && (
                    <div className="flex items-center gap-2.5 text-accent">
                        <Spinner size={14} data-testid="answer-spinner" label="检索中" />
                        <span className="text-sm">正在检索知识库…</span>
                    </div>
                )}

                {answer && (
                    <>
                        {isLoading && (
                            <span className="absolute right-3.5 top-3.5">
                                <Spinner size={10} data-testid="answer-spinner" label="生成中" />
                            </span>
                        )}
                        <div data-testid="answer-text" className="md-body">
                            <ReactMarkdown>{answer}</ReactMarkdown>
                            {isLoading && (
                                <span className="cursor-blink ml-0.5 inline-block h-4 w-0.5 bg-accent align-middle" />
                            )}
                        </div>
                    </>
                )}
            </div>

            {/* Sources */}
            {sources.length > 0 && (
                <div className="flex flex-col gap-2">
                    <div className="flex items-center gap-1.5 text-[11px] font-medium text-ink-3">
                        <Paperclip size={12} />
                        引用来源
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {sources.map(src => (
                            <Link
                                key={src.doc_id}
                                href={`/wiki/${src.doc_id}`}
                                data-testid={`source-badge-${src.doc_id}`}
                                className="source-badge no-underline"
                                title={`查看《${src.title ?? src.doc_id}》文档详情`}
                            >
                                <span className="font-mono text-[10px] text-accent-ink">{src.doc_id}</span>
                                {src.title && (
                                    <>
                                        <span className="text-ink-3">·</span>
                                        <span className="max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap">
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
