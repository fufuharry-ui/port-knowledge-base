'use client';
import React, { useCallback, useEffect, useRef, useState } from 'react';
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
import {
    fetchWikiIndex,
    deleteDoc,
    recompileDoc,
    getCompileErrorMessage,
    getUserFacingErrorMessage,
    type DocMeta,
    type WikiIndexData,
} from '@/lib/api';

export default function WikiPage() {
    const router = useRouter();
    const { push: pushToast } = useToast();
    const [docs, setDocs] = useState<DocMeta[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // E004: 跟踪各文档上一份快照状态;首轮快照不弹 Toast(历史 error 不算新失败)
    const statusByIdRef = useRef(new Map<string, DocMeta['status']>());
    const initializedRef = useRef(false);
    const pollInFlightRef = useRef(false);

    // 单一快照应用入口:先算状态迁移并弹 Toast,再更新 state(Updater 内无副作用)
    const applySnapshot = useCallback((data: WikiIndexData, notify: boolean) => {
        if (notify && initializedRef.current) {
            for (const doc of data.documents) {
                const previous = statusByIdRef.current.get(doc.id);
                if ((previous === 'raw' || previous === 'compiling') && doc.status === 'error') {
                    pushToast(getCompileErrorMessage(doc.error_code), 'error');
                }
            }
        }
        statusByIdRef.current = new Map(data.documents.map(doc => [doc.id, doc.status]));
        initializedRef.current = true;
        setDocs(data.documents);
        setTotal(data.total_docs);
    }, [pushToast]);

    const refreshDocs = useCallback(async (notify: boolean) => {
        const data = await fetchWikiIndex();
        applySnapshot(data, notify);
    }, [applySnapshot]);

    useEffect(() => {
        refreshDocs(false)
            .catch(e => setError(getUserFacingErrorMessage(e, '知识库加载失败，请稍后重试')))
            .finally(() => setLoading(false));
    }, [refreshDocs]);

    // E004: 任一文档编译中时,每 3s 条件轮询;全部进入终态自动停止;飞行中去重
    const hasPending = docs.some(d => d.status === 'raw' || d.status === 'compiling');
    useEffect(() => {
        if (!hasPending) return;
        const timer = window.setInterval(async () => {
            if (pollInFlightRef.current) return;
            pollInFlightRef.current = true;
            try {
                await refreshDocs(true);
            } catch {
                // 瞬时轮询失败不覆盖上一份可用目录
            } finally {
                pollInFlightRef.current = false;
            }
        }, 3000);
        return () => window.clearInterval(timer);
    }, [hasPending, refreshDocs]);

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
                    有文档编译中，每3秒自动刷新…
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
                                        statusByIdRef.current.delete(id);
                                        setDocs(ds => ds.filter(d => d.id !== id));
                                        setTotal(t => Math.max(0, t - 1));
                                        pushToast('文档已删除', 'success');
                                    } catch (e) {
                                        pushToast(getUserFacingErrorMessage(e, '删除失败，请稍后重试'), 'error');
                                    }
                                }}
                                onRecompile={async id => {
                                    try {
                                        await recompileDoc(id);
                                        // 同步更新状态跟踪与界面:下一轮轮询把 compiling→error 视作新一轮失败
                                        statusByIdRef.current.set(id, 'compiling');
                                        setDocs(current => current.map(doc => (
                                            doc.id === id
                                                ? { ...doc, status: 'compiling', error_code: undefined }
                                                : doc
                                        )));
                                        // 成功不弹 Toast:编译中提示条已反馈,结果由下一轮轮询统一通知
                                    } catch (e) {
                                        pushToast(getUserFacingErrorMessage(e, '重编译失败，请稍后重试'), 'error');
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
