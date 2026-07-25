import React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface EmptyStateProps {
    icon?: LucideIcon;
    title: string;
    description?: string;
    action?: React.ReactNode;
    className?: string;
}

/** 空状态(替换 "emoji + 一句话",带视觉锚点与引导) */
export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
    return (
        <div
            className={cn(
                'flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line-strong bg-surface px-6 py-14 text-center',
                className,
            )}
        >
            {Icon && (
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-subtle text-ink-3">
                    <Icon size={22} strokeWidth={1.75} />
                </div>
            )}
            <div>
                <p className="text-base font-medium text-ink">{title}</p>
                {description && <p className="mt-1 text-sm leading-6 text-ink-2">{description}</p>}
            </div>
            {action}
        </div>
    );
}
