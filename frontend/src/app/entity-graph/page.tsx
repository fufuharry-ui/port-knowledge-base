/**
 * /entity-graph 页 — 实体邻居图谱 (Big-Loop #4, Loop #2 能力页面可见)
 * 输入术语 + 深度 → 展示多跳邻居术语 + 关系边表格(谁依赖/属于/支撑谁)
 */
'use client';
import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { Share2 } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { fetchEntityGraph, type EntityGraphData } from '@/lib/api';

const TYPE_LABELS: Record<string, string> = {
    depends_on: '依赖',
    part_of: '属于',
    supports: '支撑',
    alternative_of: '可替代',
};

const INPUT_CLS =
    'rounded-md border border-line-strong bg-surface px-3 py-2 text-sm text-ink outline-none transition-shadow placeholder:text-ink-3 focus:border-accent focus:shadow-[0_0_0_3px_rgba(37,99,235,0.12)]';

export default function EntityGraphPage() {
    // Next.js 16: useSearchParams 需 Suspense 边界(静态预渲染要求)
    return (
        <React.Suspense fallback={<div className="py-20 text-center text-sm text-ink-3">加载中…</div>}>
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
        <div className="mx-auto max-w-5xl px-6 py-10">
            <PageHeader
                icon={Share2}
                title="实体关系图谱"
                subtitle="术语级语义关系(依赖/属于/支撑)——检索时用于扩展查询,提升召回"
                className="mb-6"
            />

            {/* Search bar */}
            <Card className="mb-5">
                <form onSubmit={lookup} className="flex flex-wrap items-center gap-2.5 p-3">
                    <input
                        value={term}
                        onChange={e => setTerm(e.target.value)}
                        placeholder="输入术语,如 5G技术 / 融合导航架构 / eMBB"
                        className={`${INPUT_CLS} min-w-[220px] flex-1`}
                    />
                    <select
                        value={depth}
                        onChange={e => setDepth(Number(e.target.value))}
                        className={`${INPUT_CLS} cursor-pointer`}
                        aria-label="跳数"
                    >
                        <option value={1}>1 跳</option>
                        <option value={2}>2 跳</option>
                        <option value={3}>3 跳</option>
                    </select>
                    <Button type="submit" disabled={loading}>
                        {loading ? '查询中…' : '探索'}
                    </Button>
                </form>
            </Card>

            {error && (
                <div
                    role="alert"
                    className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-[13px] text-danger-ink"
                >
                    ⚠ 查询失败:{error}
                </div>
            )}

            {data && (
                <div className="flex flex-col gap-4">
                    {/* Neighbors */}
                    <Card className="p-5">
                        <h2 className="mb-3 text-[13px] font-semibold text-ink">
                            「{data.term}」的 {data.depth} 跳邻居 ({data.neighbors.length} 个)
                        </h2>
                        {data.neighbors.length === 0 ? (
                            <p className="text-[13px] text-ink-3">
                                无邻居——该术语可能不在实体关系图中
                            </p>
                        ) : (
                            <div className="flex flex-wrap gap-2">
                                {data.neighbors.map(nb => (
                                    <button
                                        key={nb}
                                        type="button"
                                        onClick={() => setTerm(nb)}
                                        className="cursor-pointer rounded-full border border-accent/25 bg-accent-soft px-3 py-1 text-xs font-medium text-accent-ink transition-colors hover:bg-accent/15"
                                    >
                                        {nb}
                                    </button>
                                ))}
                            </div>
                        )}
                    </Card>

                    {/* Edges table */}
                    {data.edges.length > 0 && (
                        <Card className="overflow-auto p-5">
                            <h2 className="mb-3 text-[13px] font-semibold text-ink">
                                相关关系边 ({data.edges.length} / 全库 {data.total_edges})
                            </h2>
                            <table className="w-full border-collapse text-xs">
                                <thead>
                                    <tr className="text-left text-ink-3">
                                        <th className="border-b border-line px-2.5 py-2 font-medium">源</th>
                                        <th className="border-b border-line px-2.5 py-2 font-medium">关系</th>
                                        <th className="border-b border-line px-2.5 py-2 font-medium">目标</th>
                                        <th className="border-b border-line px-2.5 py-2 font-medium">置信度</th>
                                        <th className="border-b border-line px-2.5 py-2 font-medium">出处</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.edges.map((edge, i) => (
                                        <tr key={i} className="transition-colors hover:bg-hover">
                                            <td className="border-b border-line px-2.5 py-2 font-medium text-ink">{edge.source}</td>
                                            <td className="border-b border-line px-2.5 py-2">
                                                <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink">
                                                    {TYPE_LABELS[edge.type ?? ''] ?? edge.type}
                                                </span>
                                            </td>
                                            <td className="border-b border-line px-2.5 py-2 font-medium text-ink">{edge.target}</td>
                                            <td className="border-b border-line px-2.5 py-2 text-ink-3">
                                                {edge.confidence != null ? edge.confidence.toFixed(2) : '—'}
                                            </td>
                                            <td className="border-b border-line px-2.5 py-2 font-mono text-[11px] text-ink-3">
                                                {edge.doc_id ?? '—'}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </Card>
                    )}
                </div>
            )}

            {!data && !loading && !error && (
                <Card className="py-12 text-center text-[13px] text-ink-3">
                    输入术语并点击「探索」查看其语义邻居
                </Card>
            )}
        </div>
    );
}
