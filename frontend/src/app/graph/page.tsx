'use client';
import React, { useEffect, useState } from 'react';
import { Network } from 'lucide-react';
import KnowledgeGraph from '@/components/KnowledgeGraph';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { fetchGraph, type GraphNode, type GraphEdge } from '@/lib/api';

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
        <div className="mx-auto max-w-6xl px-6 py-10">
            <PageHeader
                icon={Network}
                title="全局知识图谱"
                subtitle="文档关系网与本体概念地图 · 力导向布局,可拖拽缩放"
                className="mb-6"
            />

            {loading && (
                <Card className="p-6">
                    <Skeleton className="h-[520px] w-full rounded-lg" />
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

            {!loading && !error && (
                <Card className="overflow-hidden p-0">
                    <KnowledgeGraph nodes={nodes} edges={edges} />
                </Card>
            )}
        </div>
    );
}
