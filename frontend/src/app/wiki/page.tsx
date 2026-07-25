'use client';
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { BookOpen, Upload, FileText, CheckCircle2, Clock } from 'lucide-react';
import WikiCard from '@/components/WikiCard';
import { PageHeader } from '@/components/ui/PageHeader';
import { StatTile } from '@/components/ui/StatTile';
import { EmptyState } from '@/components/ui/EmptyState';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { fetchWikiIndex, deleteDoc, recompileDoc, type DocMeta } from '@/lib/api';

export default function WikiPage() {
    const router = useRouter();
    const toast = useToast();
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
    const pendingCount = docs.filter(d => d.status === 'raw' || d.status === 'compiling').length;

    return (
        <div className="mx-auto max-w-6xl px-6 py-10">
            <PageHeader
                icon={BookOpen}
                title="知识库仪表盘"
                subtitle="零向量数据库 · Context Stuffing 检索引擎"
                action={
                    <Link href="/upload">
                        <Button><Upload size={16} />上传文档</Button>
                    </Link>
                }
            />

            {/* 编译中提示(保留 testid) */}
            {hasPending && (
                <p data-testid="compiling-hint" className="mt-3 flex items-center gap-2 text-[13px] font-medium text-warning-ink">
                    <span className="spin inline-block h-3 w-3 rounded-full border-2 border-warning border-t-transparent" />
                    有文档编译中,每 10 秒自动刷新…
                </p>
            )}

            {/* 统计瓦片(替代裸文本统计;保留 doc-count testid) */}
            <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
                <StatTile icon={FileText} label="文档总数" value={<span data-testid="doc-count">{total}</span>} />
                <StatTile icon={CheckCircle2} label="已编译" value={compiled} tone="success" />
                <StatTile icon={Clock} label="编译中 / 待编译" value={pendingCount} tone={pendingCount > 0 ? 'warning' : 'default'} />
            </div>

            {/* 加载:骨架屏(替代裸 spinner) */}
            {loading && (
                <div className="mt-8 grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
                    {Array.from({ length: 6 }).map((_, i) => (
                        <div key={i} className="rounded-lg border border-line bg-surface p-4 shadow-card">
                            <Skeleton className="h-5 w-20 rounded-full" />
                            <Skeleton className="mt-3 h-4 w-3/4" />
                            <Skeleton className="mt-2 h-3 w-full" />
                            <Skeleton className="mt-1.5 h-3 w-5/6" />
                        </div>
                    ))}
                </div>
            )}

            {/* 错误 */}
            {error && (
                <div role="alert" className="mt-6 rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-[13px] text-danger-ink">
                    ⚠ 加载失败:{error}
                </div>
            )}

            {/* 空态(保留 testid;带引导) */}
            {!loading && !error && docs.length === 0 && (
                <div className="mt-8" data-testid="empty-state">
                    <EmptyState
                        icon={FileText}
                        title="暂无文档"
                        description="上传第一份文件,开始构建你的港口智慧化知识库。"
                        action={
                            <Link href="/upload">
                                <Button><Upload size={16} />去上传</Button>
                            </Link>
                        }
                    />
                </div>
            )}

            {/* 卡片网格(挂载错落) */}
            {!loading && docs.length > 0 && (
                <div className="mt-8 grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
                    {docs.map((doc, i) => (
                        <div key={doc.id} className="fade-in-up" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                            <WikiCard
                                doc={doc}
                                onExpand={id => router.push(`/wiki/${id}`)}
                                onDelete={async id => {
                                    try {
                                        await deleteDoc(id);
                                        setDocs(ds => ds.filter(d => d.id !== id));
                                        setTotal(t => Math.max(0, t - 1));
                                        toast.push('文档已删除', 'success');
                                    } catch (e) {
                                        toast.push(`删除失败: ${e instanceof Error ? e.message : e}`, 'error');
                                    }
                                }}
                                onRecompile={async id => {
                                    try {
                                        await recompileDoc(id);
                                        setDocs(ds => ds.map(d => d.id === id ? { ...d, status: 'compiling' } : d));
                                        toast.push('已触发重编译,稍候自动刷新', 'info');
                                    } catch (e) {
                                        toast.push(`重编译失败: ${e instanceof Error ? e.message : e}`, 'error');
                                    }
                                }}
                            />
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
