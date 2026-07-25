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

/** 推理轨迹终端面板(保留终端美学:浅色界面中的深色"代码块"是有意的视觉锚点) */
export default function ThoughtTrace({ thoughts, isStreaming }: ThoughtTraceProps) {
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [thoughts.length]);

    if (thoughts.length === 0 && !isStreaming) return null;

    return (
        <div
            data-testid="thought-trace"
            className="my-2 max-h-40 overflow-y-auto rounded-lg border border-slate-700/60 bg-slate-900 px-4 py-3 font-mono text-xs leading-7 shadow-md"
        >
            {/* Terminal header bar */}
            <div className="mb-2 flex items-center gap-1.5 opacity-60">
                <div className="h-2 w-2 rounded-full bg-red-400" />
                <div className="h-2 w-2 rounded-full bg-amber-400" />
                <div className="h-2 w-2 rounded-full bg-emerald-400" />
                <span className="ml-1.5 text-[10px] text-slate-500">
                    knowledge-base — reasoning trace
                </span>
            </div>

            {/* Thought steps */}
            {thoughts.map((t, idx) => (
                <div
                    key={`${t.step}-${t.timestamp}`}
                    data-testid={`thought-step-${t.step}`}
                    className="fade-in-up mb-0.5 flex gap-2"
                    style={{ animationDelay: `${idx * 0.05}s` }}
                >
                    <span className="shrink-0 text-emerald-400">
                        {`[${t.step.toString().padStart(2, '0')}]`}
                    </span>
                    <span className="break-all text-slate-300">
                        {t.message}
                    </span>
                </div>
            ))}

            {/* Blinking cursor while streaming */}
            {isStreaming && (
                <div className="mb-0.5 flex gap-2">
                    <span className="text-emerald-400">{'[--]'}</span>
                    <span className="cursor-blink inline-block h-3.5 w-2 self-center bg-emerald-400" />
                </div>
            )}

            <div ref={bottomRef} />
        </div>
    );
}
