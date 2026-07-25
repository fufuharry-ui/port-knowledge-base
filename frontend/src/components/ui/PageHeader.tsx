import React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface PageHeaderProps {
    icon?: LucideIcon;
    title: string;
    subtitle?: string;
    action?: React.ReactNode;
    className?: string;
}

/** 统一页头(替换 6 个雷同 "emoji + h1 + 副标题" 手搓块) */
export function PageHeader({ icon: Icon, title, subtitle, action, className }: PageHeaderProps) {
    return (
        <div className={cn('flex flex-wrap items-start justify-between gap-4', className)}>
            <div className="flex items-start gap-3">
                {Icon && (
                    <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
                        <Icon size={20} strokeWidth={2} />
                    </div>
                )}
                <div>
                    <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
                    {subtitle && <p className="mt-1 text-sm leading-6 text-ink-2">{subtitle}</p>}
                </div>
            </div>
            {action && <div className="flex items-center gap-2">{action}</div>}
        </div>
    );
}
