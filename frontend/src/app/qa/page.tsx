'use client';
import React, { useCallback } from 'react';
import ChatPanel from '@/components/ChatPanel';

export default function QAPage() {
    // When standalone, highlight IDs are just discarded 
    // unless we decide to flash them in the UI somewhere else
    const handleHighlight = useCallback((ids: string[]) => {
        if (ids.length > 0) {
            console.log('Entities activated:', ids);
        }
    }, []);

    return (
        <div style={{ maxWidth: '800px', margin: '0 auto', padding: '40px 24px', height: 'calc(100vh - 56px)' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '32px' }}>
                <span style={{ fontSize: '28px' }}>🧠</span>
                <div>
                    <h1 style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>
                        PortGPT · 智能问答
                    </h1>
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        基于 Karpathy Context Stuffing 哲学的强推理、零向量数据库实时 Q&A 引擎
                    </p>
                </div>
            </div>

            {/* Q&A Chat Card */}
            <div className="glass-card" style={{
                padding: 0,
                height: 'calc(100% - 120px)',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
            }}>
                <ChatPanel onHighlight={handleHighlight} />
            </div>
        </div>
    );
}
