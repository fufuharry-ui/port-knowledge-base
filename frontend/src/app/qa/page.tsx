'use client';
import React, { useCallback } from 'react';
import ChatPanel from '@/components/ChatPanel';

export default function QAPage() {
    // 独立问答页暂无图谱可联动,命中文档高亮暂仅记录
    const handleHighlight = useCallback((ids: string[]) => {
        if (ids.length > 0) {
            console.log('Entities activated:', ids);
        }
    }, []);

    return (
        <div className="mx-auto flex h-[calc(100vh-56px)] max-w-4xl flex-col px-4 py-4 sm:px-6 sm:py-6">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-card">
                <ChatPanel onHighlight={handleHighlight} />
            </div>
        </div>
    );
}
