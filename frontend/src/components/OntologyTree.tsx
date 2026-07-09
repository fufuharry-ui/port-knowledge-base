/**
 * OntologyTree.tsx — 本体树递归渲染组件 (Big-Loop #4)
 * 把嵌套的 OntologyNode[] 渲染为可折叠的树状结构。
 * 纯展示组件,数据由页面注入,便于单测。
 */
'use client';
import React, { useState } from 'react';
import type { OntologyNode } from '@/lib/api';

interface OntologyTreeProps {
    nodes: OntologyNode[];
}

/** 单个节点(可折叠,有子节点时显示展开箭头) */
function TreeNode({ node, level }: { node: OntologyNode; level: number }) {
    const hasChildren = Array.isArray(node.children) && node.children!.length > 0;
    const [open, setOpen] = useState(level < 1); // 顶层默认展开

    return (
        <div data-role="ontology-node" style={{ marginLeft: level > 0 ? 18 : 0 }}>
            <div
                onClick={() => hasChildren && setOpen(o => !o)}
                style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '6px',
                    padding: '5px 8px',
                    borderRadius: '8px',
                    cursor: hasChildren ? 'pointer' : 'default',
                    transition: 'background 0.12s',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
                <span style={{
                    display: 'inline-block',
                    width: '14px',
                    fontSize: '11px',
                    color: 'var(--text-muted)',
                    lineHeight: '20px',
                    flexShrink: 0,
                }}>
                    {hasChildren ? (open ? '▼' : '▶') : '•'}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <span style={{
                        fontSize: level === 0 ? '14px' : '13px',
                        fontWeight: level === 0 ? 600 : 400,
                        color: 'var(--text-primary)',
                    }}>
                        {node.term}
                    </span>
                    {node.parent && (
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: '8px' }}>
                            ↑ {node.parent}
                        </span>
                    )}
                    {node.definition && (
                        <div data-role="ontology-def" style={{
                            fontSize: '12px',
                            color: 'var(--text-muted)',
                            marginTop: '2px',
                            lineHeight: 1.5,
                        }}>
                            {node.definition}
                        </div>
                    )}
                </div>
            </div>
            {hasChildren && open && (
                <div data-role="ontology-children">
                    {node.children!.map(child => (
                        <TreeNode key={child.term} node={child} level={level + 1} />
                    ))}
                </div>
            )}
        </div>
    );
}

export default function OntologyTree({ nodes }: OntologyTreeProps) {
    if (!nodes || nodes.length === 0) {
        return (
            <div data-role="ontology-empty" style={{
                padding: '40px', textAlign: 'center',
                color: 'var(--text-muted)', fontSize: '13px',
            }}>
                本体树为空
            </div>
        );
    }
    return (
        <div data-role="ontology-tree">
            {nodes.map(node => (
                <TreeNode key={node.term} node={node} level={0} />
            ))}
        </div>
    );
}
