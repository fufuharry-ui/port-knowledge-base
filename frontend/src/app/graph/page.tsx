'use client';
import React, { useEffect, useState } from 'react';
import KnowledgeGraph from '@/components/KnowledgeGraph';
import { fetchGraph, type GraphNode, type GraphEdge } from '@/lib/api';

const LEGEND = [
    { color: '#60a5fa', label: '补充关联' },
    { color: '#f87171', label: '矛盾冲突' },
    { color: '#34d399', label: '同类主题' },
    { color: '#a78bfa', label: '扩展说明' },
];

export default function GraphPage() {
    const [nodes, setNodes] = useState<GraphNode[]>([]);
    const [edges, setEdges] = useState<GraphEdge[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetchGraph()
            .then(data => { setNodes(data.nodes); setEdges(data.edges); })
            .catch(e => setError(e.message))
            .finally(() => setLoading(false));
    }, []);

    return (
        <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '40px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '32px' }}>
                <span style={{ fontSize: '28px' }}>🕸️</span>
                <div>
                    <h1 style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>
                        全局知识图谱
                    </h1>
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        文档关系网与本体概念地图
                    </p>
                </div>
            </div>

            {loading && (
                <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
                    <span className="spin" style={{
                        display: 'inline-block', width: '28px', height: '28px',
                        border: '3px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                    }} />
                </div>
            )}

            {error && (
                <div style={{
                    color: 'var(--accent-red)', fontSize: '13px', padding: '16px', borderRadius: '12px',
                    background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.20)',
                }}>
                    ⚠ 加载失败：{error}
                </div>
            )}

            {!loading && !error && (
                <div>
                    <div className="glass-card" style={{ overflow: 'hidden', padding: 0 }}>
                        <KnowledgeGraph
                            nodes={nodes}
                            edges={edges}
                        />
                    </div>

                    {nodes.length > 0 && (
                        <div style={{ display: 'flex', gap: '24px', marginTop: '16px', flexWrap: 'wrap' }}>
                            {LEGEND.map(({ color, label }) => (
                                <span key={label} style={{
                                    display: 'flex', alignItems: 'center', gap: '6px',
                                    fontSize: '12px', color: 'var(--text-muted)',
                                }}>
                                    <span style={{
                                        display: 'inline-block', width: '28px', height: '2px',
                                        background: color, borderRadius: '2px',
                                    }} />
                                    {label}
                                </span>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
