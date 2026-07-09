/**
 * /ontology 页 — 全局本体树可视化 (Big-Loop #4, Loop #1 能力页面可见)
 */
'use client';
import React, { useEffect, useState } from 'react';
import OntologyTree from '@/components/OntologyTree';
import { fetchOntology, type OntologyData } from '@/lib/api';

export default function OntologyPage() {
    const [data, setData] = useState<OntologyData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetchOntology()
            .then(d => setData(d))
            .catch(e => setError(e.message))
            .finally(() => setLoading(false));
    }, []);

    return (
        <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '40px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                <span style={{ fontSize: '28px' }}>🌳</span>
                <div>
                    <h1 style={{
                        fontSize: '24px', fontWeight: 700,
                        color: 'var(--text-primary)', margin: '0 0 4px',
                    }}>
                        本体知识树
                    </h1>
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        系统从文档中抽取的概念分类体系(真树结构,支持检索查询扩展)
                    </p>
                </div>
            </div>

            {/* Stats */}
            {data && (
                <div style={{ display: 'flex', gap: '16px', marginBottom: '20px', flexWrap: 'wrap' }}>
                    <Stat label="概念节点" value={data.total_nodes} />
                    <Stat label="根分类" value={data.ontology_tree.length} />
                    {data.last_updated && (
                        <Stat label="最后更新" value={data.last_updated.slice(0, 16).replace('T', ' ')} />
                    )}
                </div>
            )}

            {loading && <Spinner />}

            {error && (
                <div style={{
                    color: 'var(--accent-red)', fontSize: '13px', padding: '16px', borderRadius: '12px',
                    background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.20)',
                }}>
                    ⚠ 加载失败：{error}
                </div>
            )}

            {!loading && !error && data && (
                <div className="glass-card" style={{ padding: '20px' }}>
                    <OntologyTree nodes={data.ontology_tree} />
                </div>
            )}
        </div>
    );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="glass-card" style={{ padding: '12px 18px', minWidth: '120px' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
            <div style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>{value}</div>
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
