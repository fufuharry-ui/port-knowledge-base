'use client';
import React, { useCallback, useRef, useState } from 'react';
import ChatBubble from './ChatBubble';
import ThoughtTrace from './ThoughtTrace';
import type { CitationMeta, QAEvent } from '@/lib/qa-stream';
import { streamQA } from '@/lib/qa-stream';

interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
}

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
    const [citations, setCitations] = useState<CitationMeta[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);
    const [inputValue, setInputValue] = useState('');
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

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
        setCitations([]);
        setIsStreaming(true);

        // 添加空的 assistant 消息占位
        const assistantId = `assistant-${Date.now()}`;
        const assistantMsg: Message = {
            id: assistantId,
            role: 'assistant',
            content: '',
        };
        setMessages(prev => [...prev, assistantMsg]);
        scrollToBottom();

        try {
            for await (const event of streamQA(query, history)) {
                switch (event.type) {
                    case 'thought':
                        setThoughts(prev => [...prev, {
                            step: event.step,
                            message: event.message,
                            timestamp: Date.now(),
                        }]);
                        break;

                    case 'source':
                        setCitations(event.citations);
                        break;

                    case 'entity':
                        onHighlight?.(event.ids);
                        break;

                    case 'delta':
                        setMessages(prev => prev.map(m =>
                            m.id === assistantId
                                ? { ...m, content: m.content + event.text }
                                : m
                        ));
                        scrollToBottom();
                        break;

                    case 'done':
                        setIsStreaming(false);
                        break;
                }
            }
        } catch (err) {
            setMessages(prev => prev.map(m =>
                m.id === assistantId
                    ? { ...m, content: `⚠️ 流式请求失败: ${err}` }
                    : m
            ));
        } finally {
            setIsStreaming(false);
        }
    }, [inputValue, isStreaming, onHighlight]);

    return (
        <div
            data-testid="chat-panel"
            style={{
                display: 'flex',
                flexDirection: 'column',
                height: '100%',
                minHeight: '400px',
            }}
        >
            {/* ── 标题栏 ── */}
            <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '12px 16px',
                borderBottom: '1px solid rgba(255,255,255,0.06)',
                flexShrink: 0,
            }}>
                <span style={{ fontSize: 16 }}>🧠</span>
                <span style={{
                    fontSize: '13px',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                    letterSpacing: '0.02em',
                }}>
                    PortGPT — 智能问答
                </span>
                {isStreaming && (
                    <span style={{
                        marginLeft: 'auto',
                        fontSize: '11px',
                        color: '#34d399',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                    }}>
                        <span style={{
                            display: 'inline-block',
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: '#34d399',
                            animation: 'pulse 1.2s ease-in-out infinite',
                        }} />
                        推理中
                    </span>
                )}
            </div>

            {/* ── 消息列表 ── */}
            <div style={{
                flex: 1,
                overflowY: 'auto',
                padding: '16px',
                scrollbarWidth: 'thin',
                scrollbarColor: 'rgba(255,255,255,0.08) transparent',
            }}>
                {messages.length === 0 && (
                    <div style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        height: '200px',
                        gap: '12px',
                        color: 'var(--text-muted)',
                    }}>
                        <span style={{ fontSize: 40 }}>⚡</span>
                        <p style={{ fontSize: '13px', textAlign: 'center', lineHeight: 1.6 }}>
                            输入问题，系统将展示完整推理轨迹<br />
                            并提供带引用溯源的精准回答
                        </p>
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
                            citations={msg.role === 'assistant' ? citations : []}
                        />
                    </React.Fragment>
                ))}
                <div ref={messagesEndRef} />
            </div>

            {/* ── 输入区 ── */}
            <div style={{
                borderTop: '1px solid rgba(255,255,255,0.06)',
                padding: '12px 16px',
                flexShrink: 0,
            }}>
                <div style={{
                    display: 'flex',
                    gap: '10px',
                    alignItems: 'flex-end',
                    background: 'rgba(255,255,255,0.03)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: '14px',
                    padding: '10px 14px',
                    transition: 'border-color 0.2s',
                }}>
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
                        placeholder="输入问题，例如：岸桥远控系统的延迟要求是什么？（Enter 发送）"
                        disabled={isStreaming}
                        rows={1}
                        style={{
                            flex: 1,
                            background: 'transparent',
                            border: 'none',
                            outline: 'none',
                            fontSize: '14px',
                            color: 'var(--text-primary)',
                            fontFamily: 'inherit',
                            resize: 'none',
                            lineHeight: '1.5',
                            maxHeight: '120px',
                            overflowY: 'auto',
                        }}
                    />
                    <button
                        data-testid="chat-submit"
                        onClick={handleSubmit}
                        disabled={!inputValue.trim() || isStreaming}
                        style={{
                            width: 36,
                            height: 36,
                            borderRadius: '10px',
                            border: 'none',
                            background: inputValue.trim() && !isStreaming
                                ? 'linear-gradient(135deg, #3b82f6, #8b5cf6)'
                                : 'rgba(255,255,255,0.06)',
                            color: inputValue.trim() && !isStreaming ? '#fff' : 'rgba(255,255,255,0.25)',
                            cursor: inputValue.trim() && !isStreaming ? 'pointer' : 'default',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                            transition: 'all 0.2s',
                            fontSize: 16,
                        }}
                    >
                        {isStreaming ? '⏳' : '↑'}
                    </button>
                </div>
                <p style={{
                    fontSize: '10px',
                    color: 'var(--text-muted)',
                    margin: '6px 4px 0',
                }}>
                    Shift+Enter 换行 · 引用标记 [1][2] 可悬停查看来源文档
                </p>
            </div>

            <style>{`
                @keyframes pulse {
                    0%, 100% { opacity: 1; transform: scale(1); }
                    50% { opacity: 0.5; transform: scale(0.8); }
                }
            `}</style>
        </div>
    );
}
