import React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface StatTileProps {
    label: string;
    value: React.ReactNode;
    icon?: LucideIcon;
    tone?: 'default' | 'success' | 'warning' | 'danger' | 'accent';
    hint?: string;
    className?: string;
}

const TONE_TEXT: Record<NonNullable<StatTileProps['tone']>, string> = {
    default: 'text-ink',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
    accent: 'text-accent',
};

/** 统计瓦片(统一 /wiki、/ontology、/consistency 的统计展示) */
export function StatTile({ label, value, icon: Icon, tone = 'default', hint, className }: StatTileProps) {
    return (
        <div className={cn('rounded-lg border border-line bg-surface px-4 py-3 shadow-card', className)}>
            <div className="flex items-center gap-1.5 text-xs font-medium text-ink-3">
                {Icon && <Icon size={14} strokeWidth={2} />}
                {label}
            </div>
            <div className={cn('mt-1 text-2xl font-semibold tracking-tight', TONE_TEXT[tone])}>{value}</div>
            {hint && <div className="mt-0.5 text-xs text-ink-3">{hint}</div>}
        </div>
    );
}
