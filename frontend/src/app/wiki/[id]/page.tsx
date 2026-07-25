'use client';
/**
 * /wiki/[id] — 文档智能枢纽页 (Big-Loop #7)
 *
 * 修复:旧版 WikiCard 点击跳 /wiki/{id} 但路由不存在 → 404。
 * 本页用 DocHub 组件一站式聚合:摘要 + 实体 + 关联文档 + 矛盾。
 * 数据复用现有后端端点(/docs/{id}、/graph、/consistency),不改后端。
 */
import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import DocHub, { type RelatedDoc, type DocContradiction } from '@/components/DocHub';
import { Card } from '@/components/ui/Card';
import { Skeleton, SkeletonText } from '@/components/ui/Skeleton';
import { API_BASE, type DocMeta } from '@/lib/api';

interface GraphEdgeLike { source: string; target: string; type?: string; confidence?: number }
interface GraphNodeLike { id: string; title?: string }

export default function DocDetailPage() {
    const params = useParams<{ id: string }>();
    const docId = params?.id ?? '';

    const [doc, setDoc] = useState<DocMeta | null>(null);
    const [relatedDocs, setRelatedDocs] = useState<RelatedDoc[]>([]);
    const [contradictions, setContradictions] = useState<DocContradiction[]>([]);
    // 加载态派生自"已完成加载的 docId",避免在 effect 里同步 setState
    const [loadedId, setLoadedId] = useState<string | null>(null);
    const [notFound, setNotFound] = useState(false);

    useEffect(() => {
        if (!docId) return;
        let cancelled = false;
        // 并发取三份数据:文档详情 + 全图(筛关联)+ 矛盾报告(筛涉及本 doc)
        Promise.allSettled([
            fetch(`${API_BASE}/api/v1/docs/${docId}`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
            fetch(`${API_BASE}/api/v1/graph`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
            fetch(`${API_BASE}/api/v1/consistency`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
        ]).then(([docRes, graphRes, conRes]) => {
            if (cancelled) return;
            if (docRes.status === 'fulfilled' && docRes.value) {
                setDoc(docRes.value as DocMeta);
            } else {
                setNotFound(true);
            }
            if (graphRes.status === 'fulfilled') {
                // 筛:与本 doc 相关的边(任一端 == docId),另一端为关联文档
                const edges = (graphRes.value?.edges ?? []) as GraphEdgeLike[];
                const related: RelatedDoc[] = [];
                const titles = (graphRes.value?.nodes ?? []) as GraphNodeLike[];
                const titleMap = new Map(titles.map(n => [n.id, n.title ?? n.id]));
                for (const e of edges) {
                    if (e.source === docId || e.target === docId) {
                        const otherId = e.source === docId ? e.target : e.source;
                        related.push({
                            doc_id: otherId,
                            title: titleMap.get(otherId) ?? otherId,
                            type: e.type ?? 'related_to',
                            confidence: e.confidence,
                        });
                    }
                }
                setRelatedDocs(related);
            }
            if (conRes.status === 'fulfilled') {
                const cons = (conRes.value?.contradictions ?? []) as DocContradiction[];
                setContradictions(cons.filter(c => c.doc_a === docId || c.doc_b === docId));
            }
            setLoadedId(docId);
        });
        return () => { cancelled = true; };
    }, [docId]);

    const loading = !docId || loadedId !== docId;

    if (loading) {
        return (
            <div className="mx-auto max-w-4xl px-6 py-10">
                <Card className="mb-4 p-6">
                    <Skeleton className="h-7 w-2/3" />
                    <Skeleton className="mt-3 h-3 w-40" />
                    <SkeletonText lines={3} className="mt-5" />
                </Card>
                <Card className="p-5">
                    <Skeleton className="h-4 w-24" />
                    <SkeletonText lines={2} className="mt-3" />
                </Card>
            </div>
        );
    }

    return (
        <DocHub
            doc={doc ?? { id: docId }}
            relatedDocs={relatedDocs}
            contradictions={contradictions}
            notFound={notFound}
        />
    );
}
