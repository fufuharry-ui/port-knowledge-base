'use client';
import React, { useEffect, useState } from 'react';
import WikiCard from '@/components/WikiCard';
import { fetchWikiIndex, deleteDoc, recompileDoc, type DocMeta } from '@/lib/api';

export default function WikiPage() {
    const [docs, setDocs] = useState<DocMeta[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetchWikiIndex()
            .then(data => { setDocs(data.documents); setTotal(data.total_docs); })
            .catch(e => setError(e.message))
            .finally(() => setLoading(false));
    }, []);

    // Loop #11: 任一文档编译中时,每 10s 轮询刷新;全部编译完自动停止。
    // 解决:上传后用户需手动反复刷新才知道编译完成。
    const hasPending = docs.some(d => d.status === 'raw' || d.status === 'compiling');
    useEffect(() => {
        if (!hasPending) return;
        const timer = setInterval(() => {
            fetchWikiIndex()
                .then(data => { setDocs(data.documents); setTotal(data.total_docs); })
                .catch(() => {});
        }, 10000);
        return () => clearInterval(timer);
    }, [hasPending]);

    const compiled = docs.filter(d => d.status === 'compiled').length;

    return (
        <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '40px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '36px' }}>
                <div>
                    <h1 style={{ fontSize: '26px', fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 6px' }}>
                        知识库仪表盘
                    </h1>
                    <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
                        零向量数据库 · Context Stuffing 检索引擎
                    </p>
                    {hasPending && (
                        <p data-testid="compiling-hint" style={{
                            fontSize: '12px', color: 'var(--accent-amber)', margin: '6px 0 0',
                            display: 'flex', alignItems: 'center', gap: '6px',
                        }}>
                            <span className="spin" style={{
                                display: 'inline-block', width: '10px', height: '10px',
                                border: '1.5px solid var(--accent-amber)', borderTopColor: 'transparent',
                                borderRadius: '50%',
                            }} />
                            有文档编译中,每 10 秒自动刷新…
                        </p>
                    )}
                </div>
                <div style={{ display: 'flex', gap: '32px', textAlign: 'right' }}>
                    <div>
                        <p data-testid="doc-count" style={{ fontSize: '28px', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>{total}</p>
                        <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: 0 }}>文档总数</p>
                    </div>
                    <div>
                        <p style={{ fontSize: '28px', fontWeight: 700, color: 'var(--accent-green)', margin: 0 }}>{compiled}</p>
                        <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: 0 }}>已编译</p>
                    </div>
                </div>
            </div>

            {/* Loading */}
            {loading && (
                <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
                    <span className="spin" style={{
                        display: 'inline-block', width: '28px', height: '28px',
                        border: '3px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                    }} />
                </div>
            )}

            {/* Error */}
            {error && (
                <div style={{
                    color: 'var(--accent-red)', fontSize: '13px', padding: '16px', borderRadius: '12px',
                    background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.20)',
                }}>
                    ⚠ 加载失败：{error}
                </div>
            )}

            {/* Empty */}
            {!loading && !error && docs.length === 0 && (
                <div data-testid="empty-state" style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', padding: '80px 0',
                    color: 'var(--text-muted)', gap: '12px',
                }}>
                    <span style={{ fontSize: '48px' }}>🗄️</span>
                    <p style={{ fontSize: '13px', margin: 0 }}>暂无文档，请上传第一份文件开始构建知识库</p>
                </div>
            )}

            {/* Grid */}
            {!loading && docs.length > 0 && (
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
                    gap: '16px',
                }}>
                    {docs.map(doc => (
                        <WikiCard
                            key={doc.id}
                            doc={doc}
                            onExpand={id => window.location.assign(`/wiki/${id}`)}
                            onDelete={async id => {
                                try {
                                    await deleteDoc(id);
                                    setDocs(ds => ds.filter(d => d.id !== id));
                                    setTotal(t => Math.max(0, t - 1));
                                } catch (e) {
                                    alert(`删除失败: ${e instanceof Error ? e.message : e}`);
                                }
                            }}
                            onRecompile={async id => {
                                try {
                                    await recompileDoc(id);
                                    // 状态先标 compiling(后台编译中),稍后刷新
                                    setDocs(ds => ds.map(d => d.id === id ? { ...d, status: 'compiling' } : d));
                                } catch (e) {
                                    alert(`重编译失败: ${e instanceof Error ? e.message : e}`);
                                }
                            }}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
