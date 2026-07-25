'use client';
import React from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { TriangleAlert } from 'lucide-react';
import { Button } from './Button';
import { cn } from '@/lib/utils';

export interface ConfirmDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    description?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    /** 危险操作(删除等):确认键用 danger 变体 + 警示图标 */
    danger?: boolean;
    onConfirm: () => void;
}

/**
 * 自定义确认对话框(替换 window.confirm — 原生对话框阻塞、无样式、不可测试)。
 * testid 契约:confirm-dialog / confirm-accept / confirm-cancel(LIVE UAT test4 依赖)。
 */
export function ConfirmDialog({
    open,
    onOpenChange,
    title,
    description,
    confirmLabel = '确认',
    cancelLabel = '取消',
    danger = false,
    onConfirm,
}: ConfirmDialogProps) {
    return (
        <Dialog.Root open={open} onOpenChange={onOpenChange}>
            <Dialog.Portal>
                <Dialog.Overlay className="fade-in fixed inset-0 z-[300] bg-slate-900/40" />
                <Dialog.Content
                    data-testid="confirm-dialog"
                    onClick={e => e.stopPropagation()}
                    className={cn(
                        'fade-in-up fixed left-1/2 top-1/2 z-[301] w-[min(400px,90vw)] -translate-x-1/2 -translate-y-1/2',
                        'rounded-xl border border-line bg-surface p-5 shadow-lg',
                    )}
                >
                    <div className="flex items-start gap-3">
                        {danger && (
                            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-danger-soft text-danger">
                                <TriangleAlert size={17} strokeWidth={2} />
                            </span>
                        )}
                        <div className="min-w-0 flex-1">
                            <Dialog.Title className="text-[15px] font-semibold text-ink">
                                {title}
                            </Dialog.Title>
                            {description && (
                                <Dialog.Description className="mt-1.5 text-[13px] leading-6 text-ink-2">
                                    {description}
                                </Dialog.Description>
                            )}
                        </div>
                    </div>
                    <div className="mt-5 flex justify-end gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            data-testid="confirm-cancel"
                            onClick={() => onOpenChange(false)}
                        >
                            {cancelLabel}
                        </Button>
                        <Button
                            variant={danger ? 'danger' : 'primary'}
                            size="sm"
                            data-testid="confirm-accept"
                            onClick={() => {
                                onConfirm();
                                onOpenChange(false);
                            }}
                        >
                            {confirmLabel}
                        </Button>
                    </div>
                </Dialog.Content>
            </Dialog.Portal>
        </Dialog.Root>
    );
}
