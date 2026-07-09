'use client';
import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { GraphNode, GraphEdge } from '@/lib/api';

interface KnowledgeGraphProps {
    nodes: GraphNode[];
    edges: GraphEdge[];
    /** Q&A entity 事件推送的高亮文档 ID 列表 */
    highlightIds?: string[];
}

const EDGE_COLORS: Record<string, string> = {
    supplements: '#60a5fa',
    contradicts: '#f87171',
    same_topic: '#34d399',
    expands: '#a78bfa',
};

const HIGHLIGHT_COLOR = '#fbbf24';   // 金色高亮
const DEFAULT_COLOR = '#3b82f6';

export default function KnowledgeGraph({ nodes, edges, highlightIds = [] }: KnowledgeGraphProps) {
    const highlightSet = useMemo(() => new Set(highlightIds), [highlightIds]);
    const highlightCount = nodes.filter(n => highlightSet.has(n.id)).length;

    const option = useMemo(() => ({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: 'rgba(10,10,18,0.92)',
            borderColor: 'rgba(255,255,255,0.10)',
            textStyle: { color: '#f0f0f5', fontSize: 12 },
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
                    label: { show: true, color: '#f0f0f5', fontSize: 10, overflow: 'truncate', width: 80 },
                    itemStyle: {
                        color: isHighlighted ? HIGHLIGHT_COLOR : DEFAULT_COLOR,
                        borderColor: isHighlighted ? '#fde68a' : '#60a5fa',
                        borderWidth: isHighlighted ? 3 : 2,
                        shadowBlur: isHighlighted ? 16 : 0,
                        shadowColor: isHighlighted ? 'rgba(251,191,36,0.6)' : 'transparent',
                    },
                };
            }),
            edges: edges.map(e => ({
                source: e.source,
                target: e.target,
                type: e.type,
                confidence: e.confidence,
                lineStyle: { color: EDGE_COLORS[e.type] ?? '#6b7280', width: 1.5, curveness: 0.15, opacity: 0.7 },
                label: { show: true, formatter: e.type, fontSize: 9, color: EDGE_COLORS[e.type] ?? '#9ca3af' },
            })),
            force: { repulsion: 200, gravity: 0.1, edgeLength: [80, 200] },
            roam: true,
            emphasis: { focus: 'adjacency' },
        }],
    }), [nodes, edges, highlightSet]);

    if (nodes.length === 0) {
        return (
            <div data-testid="knowledge-graph" style={{ padding: '24px' }}>
                <div data-testid="graph-empty" style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', height: '280px',
                    color: 'var(--text-muted)', gap: '10px',
                }}>
                    <span style={{ fontSize: '40px' }}>🕸️</span>
                    <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>暂无图谱数据，请先上传并编译文档</p>
                </div>
            </div>
        );
    }

    return (
        <div data-testid="knowledge-graph" style={{ position: 'relative' }}>
            <ReactECharts
                data-testid="echarts-mock"
                data-nodes={nodes.length}
                data-highlight-count={highlightCount}
                option={option}
                style={{ height: '520px', width: '100%' }}
                notMerge
            />
            {/* Stats bar */}
            <div className="graph-legend" style={{
                position: 'absolute', bottom: '12px', right: '12px',
                display: 'flex', gap: '16px', fontSize: '12px', color: 'var(--text-muted)',
            }}>
                <span>节点: <b data-testid="node-count" style={{ color: 'var(--text-secondary)' }}>{nodes.length}</b></span>
                <span>边: <b data-testid="edge-count" style={{ color: 'var(--text-secondary)' }}>{edges.length}</b></span>
                {highlightCount > 0 && (
                    <span style={{ color: HIGHLIGHT_COLOR }}>
                        高亮: <b>{highlightCount}</b>
                    </span>
                )}
            </div>
        </div>
    );
}
