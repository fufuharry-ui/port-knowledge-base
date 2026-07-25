'use client';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { Kbd } from '@/components/ui/Kbd';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/utils';

interface SearchBoxProps {
    onSubmit: (query: string) => void;
    isLoading?: boolean;
    placeholder?: string;
}

export default function SearchBox({
    onSubmit,
    isLoading = false,
    placeholder = '请输入问题,例如:岸桥远控系统的延迟要求是什么?',
}: SearchBoxProps) {
    const [value, setValue] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                inputRef.current?.focus();
            }
        };
        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, []);

    const handleSubmit = useCallback(() => {
        const trimmed = value.trim();
        if (!trimmed || isLoading) return;
        onSubmit(trimmed);
    }, [value, isLoading, onSubmit]);

    const canSubmit = Boolean(value.trim()) && !isLoading;

    return (
        <div className="relative w-full">
            {/* search-input-wrap:globals 中的 focus-within 光环(保留 focus-glow 契约) */}
            <div className="search-input-wrap flex items-center gap-3 px-[18px] py-3.5">
                <Search size={18} strokeWidth={2} className="shrink-0 text-ink-3" />

                <input
                    ref={inputRef}
                    role="textbox"
                    type="text"
                    value={value}
                    onChange={e => setValue(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSubmit()}
                    placeholder={placeholder}
                    disabled={isLoading}
                    className="flex-1 border-none bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-3 disabled:opacity-60"
                />

                {isLoading ? (
                    <Spinner size={16} data-testid="search-spinner" label="检索中" />
                ) : (
                    <button
                        type="button"
                        onClick={handleSubmit}
                        disabled={!canSubmit}
                        className={cn(
                            'shrink-0 rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors',
                            canSubmit
                                ? 'bg-accent text-white hover:bg-accent-hover'
                                : 'cursor-default bg-subtle text-ink-3',
                        )}
                    >
                        搜索
                    </button>
                )}
            </div>

            {/* Keyboard hint */}
            <div className="absolute -bottom-6 right-0 flex items-center gap-1 text-[10px] text-ink-3">
                <Kbd>Ctrl</Kbd>
                <span>+</span>
                <Kbd>K</Kbd>
                <span className="ml-0.5">快速唤醒</span>
            </div>
        </div>
    );
}
