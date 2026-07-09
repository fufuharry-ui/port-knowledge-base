/**
 * /consistency 页 — 一致性稽核面板 (Big-Loop #4, Loop #3 能力页面可见)
 * 展示已知矛盾列表 + 候选对数 + "触发稽核"按钮(POST)。
 * 矛盾为 0 时诚实展示"库内一致"(ADR-14)。
 */
'use client';
import React, { useEffect, useState, useCallback } from 'react';
import {
    fetchConsistency, triggerConsistencyCheck, type ConsistencyReport,
} from '@/lib/api';

export default function ConsistencyPage() {
    const [report, setReport] = useState<ConsistencyReport | null>(null);
    const [loading, setLoading] = useState(true);
    const [checking, setChecking] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setReport(await fetchConsistency());
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const runCheck = async () => {
        setChecking(true);
        setError(null);
        try {
            const result = await triggerConsistencyCheck();
            setReport(result);
            if (result.status === 'error') {
                setError(result.message ?? '稽核失败');
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setChecking(false);
        }
    };

    const contradictions = report?.contradictions ?? [];
    const total = report?.total ?? 0;
    const candidates = report?.candidates_checked ?? 0;

    return (
        <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '40px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                <span style={{ fontSize: '28px' }}>🔍</span>
                <div style={{ flex: 1 }}>
                    <h1 style={{
                        fontSize: '24px', fontWeight: 700,
                        color: 'var(--text-primary)', margin: '0 0 4px',
                    }}>
                        一致性稽核
                    </h1>
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        跨文档矛盾检出——当多文档对同一事实有冲突论断时,提示用户甄别
                    </p>
                </div>
                <button
                    onClick={runCheck}
                    disabled={checking}
                    style={{
                        padding: '10px 20px', borderRadius: '10px', border: 'none',
                        background: checking ? 'rgba(255,255,255,0.08)' : 'var(--accent-blue)',
                        color: '#fff', fontSize: '13px', fontWeight: 600,
                        cursor: checking ? 'wait' : 'pointer', whiteSpace: 'nowrap',
                    }}
                >
                    {checking ? '稽核中…' : '🔄 触发稽核'}
                </button>
            </div>

            {/* Stats */}
            <div style={{ display: 'flex', gap: '16px', marginBottom: '20px', flexWrap: 'wrap' }}>
                <StatCard
                    label="检出矛盾"
                    value={total}
                    tone={total > 0 ? 'red' : 'green'}
                />
                <StatCard label="稽核候选对" value={candidates} tone="neutral" />
                {report?.last_updated && (
                    <StatCard
                        label="最后稽核"
                        value={report.last_updated.slice(0, 16).replace('T', ' ')}
                        tone="neutral"
                    />
                )}
            </div>

            {loading && <Spinner />}

            {error && (
                <div style={{
                    color: 'var(--accent-red)', fontSize: '13px', padding: '16px', borderRadius: '12px',
                    background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.20)',
                    marginBottom: '16px',
                }}>
                    ⚠ {error}
                </div>
            )}

            {!loading && !error && report && (
                <div className="glass-card" style={{ padding: '20px' }}>
                    {total === 0 ? (
                        <div data-role="consistency-clean" style={{
                            textAlign: 'center', padding: '32px',
                            color: 'var(--accent-green, #34d399)', fontSize: '14px',
                        }}>
                            ✅ 知识库内一致,未检出矛盾
                            {candidates > 0 && (
                                <div style={{
                                    fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px',
                                }}>
                                    (已比对 {candidates} 对候选文档)
                                </div>
                            )}
                        </div>
                    ) : (
                        <div data-role="consistency-conflicts">
                            <div style={{
                                color: 'var(--accent-red)', fontSize: '13px',
                                marginBottom: '14px', fontWeight: 600,
                            }}>
                                ⚠️ 检出 {total} 处跨文档矛盾:
                            </div>
                            {contradictions.map((c, i) => (
                                <div key={i} style={{
                                    padding: '12px 14px', marginBottom: '10px', borderRadius: '10px',
                                    background: 'rgba(248,113,113,0.06)',
                                    border: '1px solid rgba(248,113,113,0.18)',
                                }}>
                                    <div style={{
                                        display: 'flex', gap: '8px', alignItems: 'center',
                                        marginBottom: '6px', flexWrap: 'wrap',
                                    }}>
                                        <code style={{
                                            fontSize: '12px', color: 'var(--text-primary)',
                                            background: 'rgba(255,255,255,0.06)', padding: '2px 6px', borderRadius: '4px',
                                        }}>{c.doc_a}</code>
                                        <span style={{ color: 'var(--text-muted)' }}>↔</span>
                                        <code style={{
                                            fontSize: '12px', color: 'var(--text-primary)',
                                            background: 'rgba(255,255,255,0.06)', padding: '2px 6px', borderRadius: '4px',
                                        }}>{c.doc_b}</code>
                                        {c.confidence != null && (
                                            <span style={{
                                                fontSize: '11px', color: 'var(--text-muted)',
                                                marginLeft: 'auto',
                                            }}>
                                                置信度 {c.confidence.toFixed(2)}
                                            </span>
                                        )}
                                    </div>
                                    {c.conflict_point && (
                                        <div style={{ fontSize: '13px', color: 'var(--text-primary)', marginBottom: '4px' }}>
                                            <strong>冲突点:</strong> {c.conflict_point}
                                        </div>
                                    )}
                                    {c.reasoning_chain && (
                                        <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
                                            {c.reasoning_chain}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function StatCard({ label, value, tone }: {
    label: string; value: React.ReactNode;
    tone: 'red' | 'green' | 'neutral';
}) {
    const color = tone === 'red' ? 'var(--accent-red)'
        : tone === 'green' ? 'var(--accent-green, #34d399)'
        : 'var(--text-primary)';
    return (
        <div className="glass-card" style={{ padding: '12px 18px', minWidth: '120px' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
            <div style={{ fontSize: '18px', fontWeight: 600, color }}>{value}</div>
        </div>
    );
}

function Spinner() {
    return (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
            <span className="spin" style={{
                display: 'inline-block', width: '28px', height: '28px',
                border: '3px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
            }} />
        </div>
    );
}
