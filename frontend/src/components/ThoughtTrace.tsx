'use client';
import React, { useEffect, useRef } from 'react';

interface ThoughtStep {
    step: number;
    message: string;
    timestamp: number;
}

interface ThoughtTraceProps {
    thoughts: ThoughtStep[];
    isStreaming: boolean;
}

export default function ThoughtTrace({ thoughts, isStreaming }: ThoughtTraceProps) {
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [thoughts.length]);

    if (thoughts.length === 0 && !isStreaming) return null;

    return (
        <div
            data-testid="thought-trace"
            style={{
                margin: '8px 0 12px 0',
                padding: '12px 16px',
                background: 'rgba(0,0,0,0.35)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: '12px',
                fontFamily: 'var(--font-mono, "JetBrains Mono", "Fira Code", monospace)',
                fontSize: '12px',
                lineHeight: '1.8',
                maxHeight: '160px',
                overflowY: 'auto',
                scrollbarWidth: 'thin',
                scrollbarColor: 'rgba(255,255,255,0.1) transparent',
            }}
        >
            {/* Terminal header bar */}
            <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                marginBottom: '8px',
                opacity: 0.5,
            }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#f87171' }} />
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#fbbf24' }} />
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#34d399' }} />
                <span style={{ marginLeft: 6, color: 'rgba(255,255,255,0.3)', fontSize: 10 }}>
                    portgpt — reasoning trace
                </span>
            </div>

            {/* Thought steps */}
            {thoughts.map((t, idx) => (
                <div
                    key={`${t.step}-${t.timestamp}`}
                    data-testid={`thought-step-${t.step}`}
                    style={{
                        display: 'flex',
                        gap: '8px',
                        animation: 'fadeInUp 0.3s ease both',
                        animationDelay: `${idx * 0.05}s`,
                        marginBottom: '2px',
                    }}
                >
                    <span style={{ color: '#34d399', flexShrink: 0 }}>
                        {`[${t.step.toString().padStart(2, '0')}]`}
                    </span>
                    <span style={{ color: 'rgba(240,240,245,0.75)', wordBreak: 'break-all' }}>
                        {t.message}
                    </span>
                </div>
            ))}

            {/* Blinking cursor while streaming */}
            {isStreaming && (
                <div style={{ display: 'flex', gap: '8px', marginBottom: '2px' }}>
                    <span style={{ color: '#34d399' }}>{'[--]'}</span>
                    <span style={{
                        display: 'inline-block',
                        width: 8,
                        height: 14,
                        background: '#34d399',
                        animation: 'blink 1s step-end infinite',
                        verticalAlign: 'middle',
                    }} />
                </div>
            )}

            <div ref={bottomRef} />

            <style>{`
                @keyframes fadeInUp {
                    from { opacity: 0; transform: translateY(4px); }
                    to   { opacity: 1; transform: translateY(0); }
                }
                @keyframes blink {
                    0%, 100% { opacity: 1; }
                    50%       { opacity: 0; }
                }
            `}</style>
        </div>
    );
}
