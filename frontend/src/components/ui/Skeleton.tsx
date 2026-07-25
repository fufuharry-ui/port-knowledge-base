import React from 'react';
import { cn } from '@/lib/utils';

/** 骨架屏(搭配 globals.css 的 .skeleton shimmer) */
export function Skeleton({ className }: { className?: string }) {
    return <div className={cn('skeleton', className)} aria-hidden="true" />;
}

/** 文本骨架行 */
export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
    return (
        <div className={cn('flex flex-col gap-2', className)} aria-hidden="true">
            {Array.from({ length: lines }).map((_, i) => (
                <Skeleton key={i} className={cn('h-3.5', i === lines - 1 ? 'w-2/3' : 'w-full')} />
            ))}
        </div>
    );
}
