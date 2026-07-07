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
import { API_BASE, type DocMeta } from '@/lib/api';

export default function DocDetailPage() {
    const params = useParams<{ id: string }>();
    const docId = params?.id ?? '';

    const [doc, setDoc] = useState<DocMeta | null>(null);
    const [relatedDocs, setRelatedDocs] = useState<RelatedDoc[]>([]);
    const [contradictions, setContradictions] = useState<DocContradiction[]>([]);
    const [loading, setLoading] = useState(true);
    const [notFound, setNotFound] = useState(false);

    useEffect(() => {
        if (!docId) return;
        setLoading(true);
        // 并发取三份数据:文档详情 + 全图(筛关联)+ 矛盾报告(筛涉及本 doc)
        Promise.allSettled([
            fetch(`${API_BASE}/api/v1/docs/${docId}`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
            fetch(`${API_BASE}/api/v1/graph`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
            fetch(`${API_BASE}/api/v1/consistency`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
        ]).then(([docRes, graphRes, conRes]) => {
            if (docRes.status === 'fulfilled' && docRes.value) {
                setDoc(docRes.value as DocMeta);
            } else {
                setNotFound(true);
            }
            if (graphRes.status === 'fulfilled') {
                // 筛:与本 doc 相关的边(任一端 == docId),另一端为关联文档
                const edges = (graphRes.value?.edges ?? []) as any[];
                const related: RelatedDoc[] = [];
                const titles = (graphRes.value?.nodes ?? []) as any[];
                const titleMap = new Map(titles.map((n: any) => [n.id, n.title ?? n.id]));
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
        }).finally(() => setLoading(false));
    }, [docId]);

    if (loading) {
        return (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '120px 0' }}>
                <span className="spin" style={{
                    display: 'inline-block', width: '28px', height: '28px',
                    border: '3px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                }} />
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
