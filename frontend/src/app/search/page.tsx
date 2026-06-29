'use client';
import React, { useState, useCallback, useRef } from 'react';
import SearchBox from '@/components/SearchBox';
import SearchResult from '@/components/SearchResult';
import { startStreamSearch } from '@/lib/sse';
import type { SearchSource } from '@/lib/api';

export default function SearchPage() {
    const [answer, setAnswer] = useState('');
    const [sources, setSources] = useState<SearchSource[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const abortRef = useRef<AbortController | null>(null);
    const fullAnswerRef = useRef('');

    const handleSubmit = useCallback((query: string) => {
        abortRef.current?.abort();
        setAnswer('');
        setSources([]);
        setIsLoading(true);
        fullAnswerRef.current = '';

        const controller = startStreamSearch(query, {
            onDelta: (delta) => {
                fullAnswerRef.current += delta;
                setAnswer(fullAnswerRef.current);
            },
            onDone: () => {
                setIsLoading(false);
                const sourceMatches = [...fullAnswerRef.current.matchAll(/\[(doc_\w+)\]/g)];
                if (sourceMatches.length > 0) {
                    setSources(sourceMatches.map(m => ({ doc_id: m[1] })));
                }
            },
            onError: (err) => {
                setAnswer(`⚠️ 检索错误: ${err.message}`);
                setIsLoading(false);
            },
        });
        abortRef.current = controller;
    }, []);

    return (
        <div style={{ maxWidth: '780px', margin: '0 auto', padding: '64px 24px', display: 'flex', flexDirection: 'column', gap: '40px' }}>
            {/* Hero */}
            <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div style={{ fontSize: '12px', color: 'var(--accent-blue)', fontFamily: 'monospace', letterSpacing: '2px', textTransform: 'uppercase' }}>
                    ⚡ Knowledge Search
                </div>
                <h1 style={{ fontSize: '32px', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
                    智能知识检索
                </h1>
                <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
                    三层渐进式检索 · BM25 初筛 → LLM 精选 → 原文注入
                </p>
            </div>

            <SearchBox onSubmit={handleSubmit} isLoading={isLoading} />
            <SearchResult answer={answer} sources={sources} isLoading={isLoading} />
        </div>
    );
}
