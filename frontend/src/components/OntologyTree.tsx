/**
 * OntologyTree.tsx — 本体树递归渲染组件 (Big-Loop #4)
 * 把嵌套的 OntologyNode[] 渲染为可折叠的树状结构。
 * 纯展示组件,数据由页面注入,便于单测。
 */
'use client';
import React, { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import type { OntologyNode } from '@/lib/api';
import { cn } from '@/lib/utils';

interface OntologyTreeProps {
    nodes: OntologyNode[];
}

/** 单个节点(可折叠,lucide chevron + 子节点计数徽标 + 键盘可达) */
function TreeNode({ node, level }: { node: OntologyNode; level: number }) {
    const hasChildren = Array.isArray(node.children) && node.children!.length > 0;
    const [open, setOpen] = useState(level < 1); // 顶层默认展开

    const toggle = () => hasChildren && setOpen(o => !o);

    return (
        <div data-role="ontology-node" className={cn(level > 0 && 'ml-4 border-l border-line pl-3')}>
            <div
                role={hasChildren ? 'button' : undefined}
                tabIndex={hasChildren ? 0 : undefined}
                aria-expanded={hasChildren ? open : undefined}
                onClick={toggle}
                onKeyDown={e => {
                    if (hasChildren && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        toggle();
                    }
                }}
                className={cn(
                    'flex items-start gap-1.5 rounded-md px-2 py-1.5 transition-colors',
                    hasChildren && 'cursor-pointer hover:bg-hover',
                )}
            >
                {/* chevron(有子节点)或圆点(叶子) */}
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center text-ink-3">
                    {hasChildren ? (
                        <ChevronRight
                            size={14}
                            strokeWidth={2.25}
                            className={cn('transition-transform duration-150', open && 'rotate-90')}
                        />
                    ) : (
                        <span className="inline-block h-1 w-1 rounded-full bg-line-strong" />
                    )}
                </span>
                <div className="min-w-0 flex-1">
                    <span
                        className={cn(
                            'text-ink',
                            level === 0 ? 'text-sm font-semibold' : 'text-[13px]',
                        )}
                    >
                        {node.term}
                    </span>
                    {hasChildren && (
                        <span className="ml-2 rounded-full bg-subtle px-1.5 py-px text-[10px] font-medium text-ink-3">
                            {node.children!.length}
                        </span>
                    )}
                    {node.parent && (
                        <span className="ml-2 text-[11px] text-ink-3">↑ {node.parent}</span>
                    )}
                    {node.definition && (
                        <div data-role="ontology-def" className="mt-0.5 text-xs leading-5 text-ink-3">
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
            <div
                data-role="ontology-empty"
                className="rounded-xl border border-dashed border-line-strong bg-surface px-6 py-12 text-center text-[13px] text-ink-3"
            >
                本体树为空
            </div>
        );
    }
    return (
        <div data-role="ontology-tree" className="flex flex-col gap-1">
            {nodes.map(node => (
                <TreeNode key={node.term} node={node} level={0} />
            ))}
        </div>
    );
}
