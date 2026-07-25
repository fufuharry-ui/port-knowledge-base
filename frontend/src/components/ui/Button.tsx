'use client';
import React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
    'inline-flex items-center justify-center gap-2 font-medium rounded-md transition-colors focus-visible:outline-none disabled:opacity-50 disabled:pointer-events-none cursor-pointer select-none',
    {
        variants: {
            variant: {
                primary: 'bg-accent text-white hover:bg-accent-hover shadow-xs',
                secondary: 'bg-subtle text-ink border border-line hover:bg-hover',
                outline: 'bg-surface text-ink border border-line-strong hover:bg-hover',
                ghost: 'text-ink-2 hover:bg-hover',
                danger: 'bg-danger text-white hover:bg-danger-ink shadow-xs',
                dangerOutline: 'bg-surface text-danger border border-danger/30 hover:bg-danger-soft',
            },
            size: {
                sm: 'h-8 px-3 text-xs',
                md: 'h-9 px-4 text-sm',
                lg: 'h-11 px-5 text-base',
                icon: 'h-8 w-8 text-sm',
            },
        },
        defaultVariants: { variant: 'primary', size: 'md' },
    },
);

export interface ButtonProps
    extends React.ButtonHTMLAttributes<HTMLButtonElement>,
        VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant, size, type = 'button', ...props }, ref) => (
        <button
            ref={ref}
            type={type}
            className={cn(buttonVariants({ variant, size }), className)}
            {...props}
        />
    ),
);
Button.displayName = 'Button';
