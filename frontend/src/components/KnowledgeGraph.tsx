'use client';
import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Network } from 'lucide-react';
import type { GraphNode, GraphEdge } from '@/lib/api';
import { EmptyState } from '@/components/ui/EmptyState';

interface KnowledgeGraphProps {
    nodes: GraphNode[];
    edges: GraphEdge[];
    /** Q&A entity 事件推送的高亮文档 ID 列表 */
    highlightIds?: string[];
}

/* 关系类型配色(浅色可读,语义对齐 design tokens) */
const EDGE_COLORS: Record<string, string> = {
    supplements: '#2563eb',   // accent 蓝
    contradicts: '#dc2626',   // danger 红
    same_topic: '#16a34a',    // success 绿
    expands: '#9333ea',       // 紫(扩展)
};

const RELATION_LABELS: Record<string, string> = {
    supplements: '补充',
    contradicts: '矛盾',
    same_topic: '同主题',
    expands: '扩展',
};

const HIGHLIGHT_COLOR = '#f59e0b';  // 琥珀高亮
const HIGHLIGHT_BORDER = '#fbbf24';
const DEFAULT_COLOR = '#2563eb';
const DEFAULT_BORDER = '#93c5fd';

export default function KnowledgeGraph({ nodes, edges, highlightIds = [] }: KnowledgeGraphProps) {
    const highlightSet = useMemo(() => new Set(highlightIds), [highlightIds]);
    const highlightCount = nodes.filter(n => highlightSet.has(n.id)).length;
    const relationTypes = useMemo(
        () => [...new Set(edges.map(e => e.type))],
        [edges],
    );

    const option = useMemo(() => ({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: '#ffffff',
            borderColor: '#e5e7eb',
            textStyle: { color: '#0f172a', fontSize: 12 },
            extraCssText: 'box-shadow: 0 4px 12px rgb(15 23 42 / 0.10); border-radius: 8px;',
        },
        series: [{
            type: 'graph',
            layout: 'force',
            animation: true,
            data: nodes.map(n => {
                const isHighlighted = highlightSet.has(n.id);
                return {
                    id: n.id,
                    name: n.title ?? n.id,
                    symbolSize: isHighlighted ? 56 : 40,
                    label: { show: true, color: '#475569', fontSize: 10, overflow: 'truncate', width: 80 },
                    itemStyle: {
                        color: isHighlighted ? HIGHLIGHT_COLOR : DEFAULT_COLOR,
                        borderColor: isHighlighted ? HIGHLIGHT_BORDER : DEFAULT_BORDER,
                        borderWidth: isHighlighted ? 3 : 2,
                        shadowBlur: isHighlighted ? 14 : 6,
                        shadowColor: isHighlighted ? 'rgba(245,158,11,0.45)' : 'rgba(37,99,235,0.20)',
                    },
                };
            }),
            edges: edges.map(e => ({
                source: e.source,
                target: e.target,
                type: e.type,
                confidence: e.confidence,
                lineStyle: { color: EDGE_COLORS[e.type] ?? '#94a3b8', width: 1.5, curveness: 0.15, opacity: 0.65 },
                label: { show: true, formatter: RELATION_LABELS[e.type] ?? e.type, fontSize: 9, color: EDGE_COLORS[e.type] ?? '#94a3b8' },
            })),
            force: { repulsion: 200, gravity: 0.1, edgeLength: [80, 200] },
            roam: true,
            emphasis: { focus: 'adjacency' },
        }],
    }), [nodes, edges, highlightSet]);

    if (nodes.length === 0) {
        return (
            <div data-testid="knowledge-graph" className="p-6">
                <div data-testid="graph-empty">
                    <EmptyState
                        icon={Network}
                        title="暂无图谱数据"
                        description="上传并编译文档后,系统将自动构建跨文档语义关联图谱。"
                    />
                </div>
            </div>
        );
    }

    return (
        <div data-testid="knowledge-graph" className="relative">
            <ReactECharts
                data-testid="echarts-mock"
                data-nodes={nodes.length}
                data-highlight-count={highlightCount}
                option={option}
                style={{ height: '520px', width: '100%' }}
                notMerge
            />
            {/* Stats + 关系类型图例 */}
            <div className="graph-legend absolute bottom-3 right-3 flex items-center gap-4 text-xs text-ink-3">
                {relationTypes.map(t => (
                    <span key={t} className="flex items-center gap-1.5">
                        <span
                            className="inline-block h-2 w-2 rounded-full"
                            style={{ background: EDGE_COLORS[t] ?? '#94a3b8' }}
                        />
                        {RELATION_LABELS[t] ?? t}
                    </span>
                ))}
                <span className="border-l border-line pl-4">
                    节点: <b data-testid="node-count" className="font-semibold text-ink-2">{nodes.length}</b>
                </span>
                <span>
                    边: <b data-testid="edge-count" className="font-semibold text-ink-2">{edges.length}</b>
                </span>
                {highlightCount > 0 && (
                    <span style={{ color: HIGHLIGHT_COLOR }}>
                        高亮: <b>{highlightCount}</b>
                    </span>
                )}
            </div>
        </div>
    );
}
