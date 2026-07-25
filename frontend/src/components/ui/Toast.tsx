'use client';
import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import { CheckCircle2, XCircle, Info, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export type ToastType = 'success' | 'error' | 'info';

interface ToastItem {
    id: number;
    message: string;
    type: ToastType;
}

interface ToastContextValue {
    push: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextValue>({ push: () => {} });

/** 在客户端组件中取 toast:const toast = useToast(); toast.push('已删除','success') */
export function useToast() {
    return useContext(ToastContext);
}

const TYPE_STYLE: Record<ToastType, { icon: React.ComponentType<{ size?: number; className?: string }>; cls: string }> = {
    success: { icon: CheckCircle2, cls: 'border-success/25 bg-surface text-success-ink' },
    error: { icon: XCircle, cls: 'border-danger/25 bg-surface text-danger-ink' },
    info: { icon: Info, cls: 'border-info/25 bg-surface text-info-ink' },
};

/**
 * 轻量 toast(替换 alert() — 非阻塞、可堆叠、4s 自动消失)。
 * 挂在 RootLayout,全站可用。
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
    const [toasts, setToasts] = useState<ToastItem[]>([]);
    const nextId = useRef(1);
    const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

    const dismiss = useCallback((id: number) => {
        setToasts(prev => prev.filter(t => t.id !== id));
        const timer = timers.current.get(id);
        if (timer) {
            clearTimeout(timer);
            timers.current.delete(id);
        }
    }, []);

    const push = useCallback((message: string, type: ToastType = 'info') => {
        const id = nextId.current++;
        setToasts(prev => [...prev.slice(-3), { id, message, type }]); // 最多 4 条
        timers.current.set(id, setTimeout(() => dismiss(id), 4000));
    }, [dismiss]);

    return (
        <ToastContext.Provider value={{ push }}>
            {children}
            <div className="pointer-events-none fixed bottom-5 right-5 z-[400] flex w-[min(360px,90vw)] flex-col gap-2">
                {toasts.map(t => {
                    const { icon: Icon, cls } = TYPE_STYLE[t.type];
                    return (
                        <div
                            key={t.id}
                            data-testid="toast"
                            data-type={t.type}
                            role={t.type === 'error' ? 'alert' : 'status'}
                            className={cn(
                                'fade-in-up pointer-events-auto flex items-start gap-2.5 rounded-lg border px-4 py-3 shadow-lg',
                                cls,
                            )}
                        >
                            <Icon size={16} className="mt-0.5 shrink-0" />
                            <p className="min-w-0 flex-1 text-[13px] leading-5 text-ink">{t.message}</p>
                            <button
                                type="button"
                                aria-label="关闭通知"
                                onClick={() => dismiss(t.id)}
                                className="shrink-0 rounded-sm p-0.5 text-ink-3 transition-colors hover:bg-hover hover:text-ink"
                            >
                                <X size={13} />
                            </button>
                        </div>
                    );
                })}
            </div>
        </ToastContext.Provider>
    );
}
