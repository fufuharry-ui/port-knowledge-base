'use client';
import React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
    'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium leading-5',
    {
        variants: {
            variant: {
                neutral: 'bg-subtle text-ink-2 border-line',
                accent: 'bg-accent-soft text-accent-ink border-accent/25',
                compiled: 'bg-success-soft text-success-ink border-success/25',
                raw: 'bg-subtle text-ink-2 border-line',
                compiling: 'bg-warning-soft text-warning-ink border-warning/25',
                error: 'bg-danger-soft text-danger-ink border-danger/25',
            },
        },
        defaultVariants: { variant: 'neutral' },
    },
);

/** 文档状态 → Badge variant 的语义映射(替代原 ASCII 状态符) */
export const STATUS_BADGE_VARIANT = {
    compiled: 'compiled',
    raw: 'raw',
    compiling: 'compiling',
    error: 'error',
} as const;

export const STATUS_BADGE_LABEL = {
    compiled: '已编译',
    raw: '待编译',
    compiling: '编译中',
    error: '编译失败',
} as const;

export interface BadgeProps
    extends React.HTMLAttributes<HTMLSpanElement>,
        VariantProps<typeof badgeVariants> {}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
    ({ className, variant, ...props }, ref) => (
        <span ref={ref} className={cn(badgeVariants({ variant }), className)} {...props} />
    ),
);
Badge.displayName = 'Badge';
