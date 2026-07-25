/**
 * /ontology 页 — 全局本体树可视化 (Big-Loop #4, Loop #1 能力页面可见)
 */
'use client';
import React, { useEffect, useState } from 'react';
import { ListTree, GitFork, Clock } from 'lucide-react';
import OntologyTree from '@/components/OntologyTree';
import { PageHeader } from '@/components/ui/PageHeader';
import { StatTile } from '@/components/ui/StatTile';
import { Card } from '@/components/ui/Card';
import { Skeleton, SkeletonText } from '@/components/ui/Skeleton';
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
        <div className="mx-auto max-w-5xl px-6 py-10">
            <PageHeader
                icon={ListTree}
                title="本体知识树"
                subtitle="系统从文档中抽取的概念分类体系(真树结构,支持检索查询扩展)"
                className="mb-6"
            />

            {/* Stats */}
            {data && !loading && (
                <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
                    <StatTile icon={ListTree} label="概念节点" value={data.total_nodes} />
                    <StatTile icon={GitFork} label="根分类" value={data.ontology_tree.length} />
                    {data.last_updated && (
                        <StatTile
                            icon={Clock}
                            label="最后更新"
                            value={
                                <span className="text-sm">
                                    {data.last_updated.slice(0, 16).replace('T', ' ')}
                                </span>
                            }
                        />
                    )}
                </div>
            )}

            {loading && (
                <Card className="p-6">
                    <Skeleton className="h-5 w-40" />
                    <SkeletonText lines={5} className="mt-4" />
                </Card>
            )}

            {error && (
                <div
                    role="alert"
                    className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-[13px] text-danger-ink"
                >
                    ⚠ 加载失败:{error}
                </div>
            )}

            {!loading && !error && data && (
                <Card className="p-5">
                    <OntologyTree nodes={data.ontology_tree} />
                </Card>
            )}
        </div>
    );
}
