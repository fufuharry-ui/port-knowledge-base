/**
 * /entity-graph 页 — 实体邻居图谱 (Big-Loop #4, Loop #2 能力页面可见)
 * 输入术语 + 深度 → 展示多跳邻居术语 + 关系边表格(谁依赖/属于/支撑谁)
 */
'use client';
import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { fetchEntityGraph, type EntityGraphData } from '@/lib/api';

const TYPE_LABELS: Record<string, string> = {
    depends_on: '依赖',
    part_of: '属于',
    supports: '支撑',
    alternative_of: '可替代',
};

export default function EntityGraphPage() {
    // Next.js 16: useSearchParams 需 Suspense 边界(静态预渲染要求)
    return (
        <React.Suspense fallback={<div style={{ padding: '80px', textAlign: 'center', color: 'var(--text-muted)' }}>加载中…</div>}>
            <EntityGraphContent />
        </React.Suspense>
    );
}

function EntityGraphContent() {
    const [term, setTerm] = useState('5G技术');
    const [depth, setDepth] = useState(2);
    const [data, setData] = useState<EntityGraphData | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Big-Loop #7: 接受 ?term= 预填(从文档枢纽的实体 chip 穿梭过来)
    const searchParams = useSearchParams();
    useEffect(() => {
        const t = searchParams.get('term');
        const d = searchParams.get('depth');
        if (t) {
            setTerm(t);
            if (d) setDepth(Number(d));
        }
    }, [searchParams]);

    const lookup = async (e?: React.FormEvent) => {
        e?.preventDefault();
        if (!term.trim()) return;
        setLoading(true);
        setError(null);
        try {
            const result = await fetchEntityGraph(term.trim(), depth);
            setData(result);
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : String(err));
            setData(null);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '40px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                <span style={{ fontSize: '28px' }}>🔗</span>
                <div>
                    <h1 style={{
                        fontSize: '24px', fontWeight: 700,
                        color: 'var(--text-primary)', margin: '0 0 4px',
                    }}>
                        实体关系图谱
                    </h1>
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        术语级语义关系(依赖/属于/支撑)——检索时用于扩展查询,提升召回
                    </p>
                </div>
            </div>

            {/* Search bar */}
            <form onSubmit={lookup} className="glass-card" style={{
                display: 'flex', gap: '10px', alignItems: 'center', padding: '12px', marginBottom: '20px',
            }}>
                <input
                    value={term}
                    onChange={e => setTerm(e.target.value)}
                    placeholder="输入术语,如 5G技术 / 融合导航架构 / eMBB"
                    style={{
                        flex: 1, padding: '10px 14px', borderRadius: '10px',
                        border: '1px solid rgba(255,255,255,0.12)',
                        background: 'rgba(255,255,255,0.04)', color: 'var(--text-primary)',
                        fontSize: '14px', outline: 'none',
                    }}
                />
                <select
                    value={depth}
                    onChange={e => setDepth(Number(e.target.value))}
                    style={{
                        padding: '10px 12px', borderRadius: '10px',
                        border: '1px solid rgba(255,255,255,0.12)',
                        background: 'rgba(255,255,255,0.04)', color: 'var(--text-primary)',
                        fontSize: '13px',
                    }}
                >
                    <option value={1}>1 跳</option>
                    <option value={2}>2 跳</option>
                    <option value={3}>3 跳</option>
                </select>
                <button type="submit" disabled={loading} style={{
                    padding: '10px 20px', borderRadius: '10px', border: 'none',
                    background: 'var(--accent-blue)', color: '#fff',
                    fontSize: '13px', fontWeight: 600, cursor: loading ? 'wait' : 'pointer',
                }}>
                    {loading ? '查询中…' : '探索'}
                </button>
            </form>

            {error && (
                <div style={{
                    color: 'var(--accent-red)', fontSize: '13px', padding: '16px', borderRadius: '12px',
                    background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.20)',
                }}>
                    ⚠ 查询失败：{error}
                </div>
            )}

            {data && (
                <>
                    {/* Neighbors */}
                    <div className="glass-card" style={{ padding: '18px', marginBottom: '16px' }}>
                        <div style={{
                            fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)',
                            marginBottom: '12px',
                        }}>
                            「{data.term}」的 {data.depth} 跳邻居 ({data.neighbors.length} 个)
                        </div>
                        {data.neighbors.length === 0 ? (
                            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                                无邻居——该术语可能不在实体关系图中
                            </div>
                        ) : (
                            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                {data.neighbors.map(nb => (
                                    <span key={nb} style={{
                                        padding: '5px 12px', borderRadius: '999px',
                                        fontSize: '12px',
                                        background: 'rgba(96,165,250,0.12)',
                                        border: '1px solid rgba(96,165,250,0.25)',
                                        color: 'var(--accent-blue)',
                                        cursor: 'pointer',
                                    }} onClick={() => setTerm(nb)}>
                                        {nb}
                                    </span>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Edges table */}
                    {data.edges.length > 0 && (
                        <div className="glass-card" style={{ padding: '18px', overflow: 'auto' }}>
                            <div style={{
                                fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)',
                                marginBottom: '12px',
                            }}>
                                相关关系边 ({data.edges.length} / 全库 {data.total_edges})
                            </div>
                            <table style={{
                                width: '100%', borderCollapse: 'collapse', fontSize: '12px',
                            }}>
                                <thead>
                                    <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                                        <th style={{ padding: '6px 10px', fontWeight: 500 }}>源</th>
                                        <th style={{ padding: '6px 10px', fontWeight: 500 }}>关系</th>
                                        <th style={{ padding: '6px 10px', fontWeight: 500 }}>目标</th>
                                        <th style={{ padding: '6px 10px', fontWeight: 500 }}>置信度</th>
                                        <th style={{ padding: '6px 10px', fontWeight: 500 }}>出处</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.edges.map((edge, i) => (
                                        <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                                            <td style={{ padding: '6px 10px', color: 'var(--text-primary)' }}>{edge.source}</td>
                                            <td style={{ padding: '6px 10px' }}>
                                                <span style={{
                                                    padding: '2px 8px', borderRadius: '6px',
                                                    background: 'rgba(167,139,250,0.12)',
                                                    color: 'var(--accent-purple, #a78bfa)',
                                                    fontSize: '11px',
                                                }}>
                                                    {TYPE_LABELS[edge.type ?? ''] ?? edge.type}
                                                </span>
                                            </td>
                                            <td style={{ padding: '6px 10px', color: 'var(--text-primary)' }}>{edge.target}</td>
                                            <td style={{ padding: '6px 10px', color: 'var(--text-muted)' }}>
                                                {edge.confidence != null ? edge.confidence.toFixed(2) : '—'}
                                            </td>
                                            <td style={{ padding: '6px 10px', color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: '11px' }}>
                                                {edge.doc_id ?? '—'}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}

            {!data && !loading && !error && (
                <div className="glass-card" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                    输入术语并点击「探索」查看其语义邻居
                </div>
            )}
        </div>
    );
}
