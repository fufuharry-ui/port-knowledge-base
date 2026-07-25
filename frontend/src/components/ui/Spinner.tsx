import React from 'react';
import { cn } from '@/lib/utils';

export interface SpinnerProps extends React.HTMLAttributes<HTMLSpanElement> {
    size?: number;
    label?: string;
}

/** 加载 spinner(语义化 role="status";透传 props 以支持 data-testid 等) */
export function Spinner({ size = 16, className, label = '加载中', ...props }: SpinnerProps) {
    return (
        <span
            role="status"
            aria-label={label}
            className={cn(
                'spin inline-block rounded-full border-2 border-line-strong border-t-accent',
                className,
            )}
            style={{ width: size, height: size }}
            {...props}
        />
    );
}
