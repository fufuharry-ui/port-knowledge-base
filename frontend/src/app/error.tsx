'use client';
import React, { useEffect } from 'react';
import { TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/Button';

/** 全局路由错误边界:兜底未捕获的渲染错误,提供重试 */
export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        console.error('Route error:', error);
    }, [error]);

    return (
        <div className="mx-auto flex min-h-[50vh] max-w-md flex-col items-center justify-center gap-4 px-6 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-danger-soft text-danger">
                <TriangleAlert size={22} strokeWidth={2} />
            </span>
            <div>
                <h2 className="text-base font-semibold text-ink">页面出错了</h2>
                <p className="mt-1.5 text-[13px] leading-6 text-ink-2">
                    {error.message || '发生未知错误,请重试。'}
                </p>
            </div>
            <Button variant="outline" onClick={reset}>
                重试
            </Button>
        </div>
    );
}
