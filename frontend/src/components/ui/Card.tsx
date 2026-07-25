'use client';
import React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const cardVariants = cva('rounded-lg border', {
    variants: {
        variant: {
            default: 'bg-surface border-line shadow-card',
            interactive:
                'bg-surface border-line shadow-card transition-[box-shadow,border-color,transform] duration-200 hover:shadow-md hover:border-line-strong hover:-translate-y-0.5 cursor-pointer',
            subtle: 'bg-subtle border-line',
        },
    },
    defaultVariants: { variant: 'default' },
});

export interface CardProps
    extends React.HTMLAttributes<HTMLDivElement>,
        VariantProps<typeof cardVariants> {}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
    ({ className, variant, ...props }, ref) => (
        <div ref={ref} className={cn(cardVariants({ variant }), className)} {...props} />
    ),
);
Card.displayName = 'Card';
