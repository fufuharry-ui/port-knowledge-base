'use client';
import React, { useCallback, useRef, useState } from 'react';
import { BrainCircuit, ArrowUp, MessageSquareText, Square } from 'lucide-react';
import ChatBubble from './ChatBubble';
import ThoughtTrace from './ThoughtTrace';
import type { CitationMeta } from '@/lib/qa-stream';
import { streamQA } from '@/lib/qa-stream';
import { cn } from '@/lib/utils';

type AssistantStatus =
    | 'streaming'
    | 'completed'
    | 'stopped'
    | 'error';

interface UserMessage {
    id: string;
    role: 'user';
    content: string;
}

interface AssistantMessage {
    id: string;
    role: 'assistant';
    content: string;
    citations: CitationMeta[];
    status: AssistantStatus;
}

type Message = UserMessage | AssistantMessage;

interface ThoughtStep {
    step: number;
    message: string;
    timestamp: number;
}

interface ChatPanelProps {
    /** 触发图谱高亮，传递命中文档 ID 列表 */
    onHighlight?: (ids: string[]) => void;
}

export default function ChatPanel({ onHighlight }: ChatPanelProps) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [thoughts, setThoughts] = useState<ThoughtStep[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);
    const [inputValue, setInputValue] = useState('');
    const listRef = useRef<HTMLDivElement>(null);
    const stickToBottomRef = useRef(true);
    const abortRef = useRef<AbortController | null>(null);

    /** 仅当用户停留在底部附近时才跟随滚动(避免逐 token 抖动、允许回看) */
    const scrollToBottom = useCallback(() => {
        const el = listRef.current;
        if (el && stickToBottomRef.current) {
            el.scrollTop = el.scrollHeight;
        }
    }, []);

    const handleListScroll = useCallback(() => {
        const el = listRef.current;
        if (!el) return;
        stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }, []);

    /** 按 id 更新 assistant 消息;非 assistant 或 id 不匹配时原样返回 */
    const updateAssistant = useCallback((
        id: string,
        updater: (m: AssistantMessage) => AssistantMessage,
    ) => {
        setMessages(prev => prev.map(m =>
            m.id === id && m.role === 'assistant' ? updater(m) : m,
        ));
    }, []);

    /** 仅当消息仍处于 streaming 时才应用更新——终态消息的迟到写入一律丢弃 */
    const updateStreamingAssistant = useCallback((
        id: string,
        updater: (m: AssistantMessage) => AssistantMessage,
    ) => {
        updateAssistant(id, m => (m.status === 'streaming' ? updater(m) : m));
    }, [updateAssistant]);

    const handleSubmit = useCallback(async () => {
        const query = inputValue.trim();
        if (!query || isStreaming) return;

        // 添加用户消息
        const userMsg: Message = {
            id: `user-${Date.now()}`,
            role: 'user',
            content: query,
        };
        // Big-Loop #8: 收集当前轮之前的对话历史(不含刚加的本轮 userMsg),
        // 传给后端解析追问代词。截断到最近 10 条(与后端一致)。
        const history = messages
            .slice(-10)
            .map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }))
            .filter(m => m.content.trim());
        setMessages(prev => [...prev, userMsg]);
        setInputValue('');
        setThoughts([]);
        setIsStreaming(true);
        stickToBottomRef.current = true;

        // 添加空的 assistant 消息占位
        const assistantId = `assistant-${Date.now()}`;
        const assistantMsg: Message = {
            id: assistantId,
            role: 'assistant',
            content: '',
            citations: [],
            status: 'streaming',
        };
        setMessages(prev => [...prev, assistantMsg]);
        requestAnimationFrame(scrollToBottom);

        const controller = new AbortController();
        abortRef.current = controller;

        try {
            for await (const event of streamQA(query, history, undefined, controller.signal)) {
                switch (event.type) {
                    case 'thought':
                        setThoughts(prev => [...prev, {
                            step: event.step,
                            message: event.message,
                            timestamp: Date.now(),
                        }]);
                        break;

                    case 'source':
                        updateStreamingAssistant(assistantId, m => ({
                            ...m,
                            citations: event.citations,
                        }));
                        break;

                    case 'entity':
                        onHighlight?.(event.ids);
                        break;

                    case 'delta':
                        updateStreamingAssistant(assistantId, m => ({
                            ...m,
                            content: m.content + event.text,
                        }));
                        scrollToBottom();
                        break;

                    case 'done':
                        updateStreamingAssistant(assistantId, m => ({
                            ...m,
                            status: 'completed',
                        }));
                        setIsStreaming(false);
                        break;
                }
            }
        } catch (err) {
            const isAbort = err instanceof Error && err.name === 'AbortError';
            setMessages(prev => prev.map(m =>
                m.id === assistantId
                    ? {
                        ...m,
                        content: isAbort
                            ? m.content + (m.content ? '\n\n' : '') + '⏹ *已停止生成*'
                            : `⚠️ 流式请求失败: ${err}`,
                    }
                    : m
            ));
        } finally {
            setIsStreaming(false);
            abortRef.current = null;
        }
    }, [inputValue, isStreaming, messages, onHighlight, scrollToBottom, updateStreamingAssistant]);

    const canSend = Boolean(inputValue.trim()) && !isStreaming;

    return (
        <div
            data-testid="chat-panel"
            className="flex h-full min-h-[400px] flex-col"
        >
            {/* ── 标题栏(统一品牌:智能知识库) ── */}
            <div className="flex shrink-0 items-center gap-2.5 border-b border-line px-4 py-3">
                <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent-soft text-accent">
                    <BrainCircuit size={15} strokeWidth={2} />
                </span>
                <div className="flex flex-col">
                    <span className="text-[13px] font-semibold tracking-tight text-ink">
                        智能知识库 · 问答
                    </span>
                    <span className="text-[10px] text-ink-3">三层渐进检索 · 引用可溯源</span>
                </div>
                {isStreaming && (
                    <span className="ml-auto flex items-center gap-1.5 text-[11px] font-medium text-success-ink">
                        <span className="pulse-soft inline-block h-1.5 w-1.5 rounded-full bg-success" />
                        推理中
                    </span>
                )}
            </div>

            {/* ── 消息列表(aria-live:流式回答对读屏可见) ── */}
            <div
                ref={listRef}
                onScroll={handleListScroll}
                aria-live="polite"
                className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
            >
                {messages.length === 0 && (
                    <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 text-center">
                        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-subtle text-ink-3">
                            <MessageSquareText size={22} strokeWidth={1.75} />
                        </span>
                        <div>
                            <p className="text-sm font-medium text-ink">向知识库提问</p>
                            <p className="mt-1 text-[13px] leading-6 text-ink-3">
                                展示完整推理轨迹,并提供带引用溯源的精准回答
                            </p>
                        </div>
                    </div>
                )}

                {messages.map((msg, idx) => (
                    <React.Fragment key={msg.id}>
                        {/* 在 assistant 消息之前显示思考轨迹（仅最后一条） */}
                        {msg.role === 'assistant' && idx === messages.length - 1 && (
                            <ThoughtTrace
                                thoughts={thoughts}
                                isStreaming={isStreaming}
                            />
                        )}
                        <ChatBubble
                            role={msg.role}
                            content={msg.content}
                            citations={msg.role === 'assistant' ? msg.citations : []}
                        />
                    </React.Fragment>
                ))}
            </div>

            {/* ── 输入区 ── */}
            <div className="shrink-0 border-t border-line px-4 py-3">
                <div
                    className={cn(
                        'flex items-end gap-2.5 rounded-xl border bg-surface px-3.5 py-2.5 transition-shadow',
                        'border-line-strong focus-within:border-accent focus-within:shadow-[0_0_0_3px_rgba(37,99,235,0.12)]',
                    )}
                >
                    <textarea
                        data-testid="chat-input"
                        value={inputValue}
                        onChange={e => setInputValue(e.target.value)}
                        onKeyDown={e => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handleSubmit();
                            }
                        }}
                        placeholder="输入问题,例如:岸桥远控系统的延迟要求是什么?(Enter 发送)"
                        disabled={isStreaming}
                        rows={1}
                        className="max-h-[120px] flex-1 resize-none border-none bg-transparent text-sm leading-6 text-ink outline-none placeholder:text-ink-3 disabled:opacity-60"
                    />
                    {isStreaming && (
                        <button
                            type="button"
                            data-testid="chat-stop"
                            onClick={() => abortRef.current?.abort()}
                            aria-label="停止生成"
                            title="停止生成"
                            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-danger/30 bg-surface text-danger transition-colors hover:bg-danger-soft"
                        >
                            <Square size={13} strokeWidth={2.5} />
                        </button>
                    )}
                    <button
                        data-testid="chat-submit"
                        onClick={handleSubmit}
                        disabled={!canSend}
                        aria-label="发送"
                        className={cn(
                            'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors',
                            canSend
                                ? 'bg-accent text-white hover:bg-accent-hover'
                                : 'cursor-default bg-subtle text-ink-3',
                        )}
                    >
                        <ArrowUp size={17} strokeWidth={2.25} />
                    </button>
                </div>
                <p className="mx-1 mb-0 mt-1.5 text-[10px] text-ink-3">
                    Shift+Enter 换行 · 引用标记可悬停查看来源文档
                </p>
            </div>
        </div>
    );
}
