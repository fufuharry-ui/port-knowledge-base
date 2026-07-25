'use client';
import React, { useState, useCallback, useRef } from 'react';
import SearchBox from '@/components/SearchBox';
import SearchResult from '@/components/SearchResult';
import { Spinner } from '@/components/ui/Spinner';
import { startStreamSearch } from '@/lib/sse';
import type { SearchSource } from '@/lib/api';

export default function SearchPage() {
    const [answer, setAnswer] = useState('');
    const [sources, setSources] = useState<SearchSource[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [thought, setThought] = useState('');
    const abortRef = useRef<AbortController | null>(null);
    const fullAnswerRef = useRef('');

    const handleSubmit = useCallback((query: string) => {
        abortRef.current?.abort();
        setAnswer('');
        setSources([]);
        setThought('');
        setIsLoading(true);
        fullAnswerRef.current = '';

        const controller = startStreamSearch(query, {
            onThought: (_step, message) => {
                setThought(message);
            },
            onDelta: (delta) => {
                fullAnswerRef.current += delta;
                setAnswer(fullAnswerRef.current);
                setThought(''); // 首字到达后清空进度提示
            },
            onDone: () => {
                setIsLoading(false);
                setThought('');
                // 真实来源行格式(api/main.py):"📎 **来源：** `doc_id` 标题 | ...";
                // 兼容旧 [doc_id] 标记。解析出 doc_id + 标题,供引用来源徽章穿梭。
                const seen = new Map<string, string | undefined>();
                for (const m of fullAnswerRef.current.matchAll(/[`[](doc_\w+)[`\]]\s*([^|\n`]*)/g)) {
                    if (!seen.has(m[1])) seen.set(m[1], m[2].trim() || undefined);
                }
                if (seen.size > 0) {
                    setSources([...seen].map(([doc_id, title]) => ({ doc_id, title })));
                }
            },
            onError: (err) => {
                setAnswer(`⚠️ 检索错误: ${err.message}`);
                setIsLoading(false);
                setThought('');
            },
        });
        abortRef.current = controller;
    }, []);

    return (
        <div className="mx-auto flex max-w-3xl flex-col gap-9 px-6 py-14">
            {/* Hero(居中层级:kicker / title / sub) */}
            <div className="flex flex-col items-center gap-2.5 text-center">
                <span className="font-mono text-[11px] font-medium uppercase tracking-[0.2em] text-accent">
                    Knowledge Search
                </span>
                <h1 className="text-[32px] font-bold tracking-tight text-ink">智能知识检索</h1>
                <p className="text-[13px] text-ink-2">
                    三层渐进式检索 · BM25 初筛 → LLM 精选 → 原文注入
                </p>
            </div>

            <SearchBox onSubmit={handleSubmit} isLoading={isLoading} />

            {/* 进度反馈:检索各阶段提示(消除 14-21s 干等焦虑) */}
            {isLoading && thought && (
                <div className="fade-in flex items-center gap-2.5 rounded-lg border border-accent/20 bg-accent-soft px-4 py-3 text-[13px] text-accent-ink">
                    <Spinner size={14} />
                    {thought}
                </div>
            )}

            <SearchResult answer={answer} sources={sources} isLoading={isLoading} />
        </div>
    );
}
