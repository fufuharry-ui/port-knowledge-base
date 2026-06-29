'use client';
import React, { useCallback, useEffect, useRef, useState } from 'react';

interface SearchBoxProps {
    onSubmit: (query: string) => void;
    isLoading?: boolean;
    placeholder?: string;
}

export default function SearchBox({
    onSubmit,
    isLoading = false,
    placeholder = '请输入问题，例如：岸桥远控系统的延迟要求是什么？',
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

    return (
        <div style={{ position: 'relative', width: '100%' }}>
            <div className="search-input-wrap" style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '14px 18px',
            }}>
                {/* Search icon */}
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="rgba(240,240,245,0.35)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                    <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>

                <input
                    ref={inputRef}
                    role="textbox"
                    type="text"
                    value={value}
                    onChange={e => setValue(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSubmit()}
                    placeholder={placeholder}
                    disabled={isLoading}
                    style={{
                        flex: 1,
                        background: 'transparent',
                        border: 'none',
                        outline: 'none',
                        fontSize: '15px',
                        color: 'var(--text-primary)',
                        fontFamily: 'inherit',
                    }}
                />

                {isLoading ? (
                    <span data-testid="search-spinner" className="spin" style={{
                        display: 'inline-block', width: '16px', height: '16px',
                        border: '2px solid var(--accent-blue)', borderTopColor: 'transparent',
                        borderRadius: '50%', flexShrink: 0,
                    }} />
                ) : (
                    <button
                        type="button"
                        onClick={handleSubmit}
                        disabled={!value.trim()}
                        style={{
                            padding: '6px 14px', borderRadius: '9px', fontSize: '12px', fontWeight: 500,
                            background: 'rgba(59,130,246,0.18)',
                            color: '#93c5fd',
                            border: '1px solid rgba(59,130,246,0.30)',
                            cursor: value.trim() ? 'pointer' : 'default',
                            opacity: value.trim() ? 1 : 0.4,
                            transition: 'all 0.15s', flexShrink: 0,
                            fontFamily: 'inherit',
                        }}
                    >
                        搜索
                    </button>
                )}
            </div>

            {/* Keyboard hint */}
            <div style={{
                position: 'absolute', bottom: '-20px', right: 0,
                fontSize: '10px', color: 'var(--text-muted)',
            }}>
                <kbd style={{ padding: '1px 5px', background: 'rgba(255,255,255,0.06)', borderRadius: '4px', border: '1px solid rgba(255,255,255,0.10)' }}>Ctrl</kbd>
                +
                <kbd style={{ padding: '1px 5px', background: 'rgba(255,255,255,0.06)', borderRadius: '4px', border: '1px solid rgba(255,255,255,0.10)' }}>K</kbd>
                {' '}快速唤醒
            </div>
        </div>
    );
}
