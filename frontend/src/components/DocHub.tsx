'use client';
/**
 * DocHub — 文档智能枢纽组件 (Big-Loop #7)
 *
 * 设计思路转变:从"8 个并列页面"到"以文档为枢纽"。
 * 用户点开任何文档(仪表盘/搜索/问答引用),在此一站式看到:
 *   摘要 + 实体(chip → 实体图谱)+ 关联文档(→ 对方文档)+ 矛盾。
 * 把推理能力集成进文档上下文,而非散落在孤立页面。
 *
 * 本组件为纯渲染(props 驱动),数据获取由 /wiki/[id]/page.tsx 负责 → 可单测。
 */
import React from 'react';
import Link from 'next/link';
import { ArrowLeft, FileText, Tags, Link2, AlertTriangle, FileSearch } from 'lucide-react';
import type { DocMeta } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge, STATUS_BADGE_VARIANT, STATUS_BADGE_LABEL } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';

export interface RelatedDoc {
    doc_id: string;
    title?: string;
    type: string;
    confidence?: number;
}

export interface DocContradiction {
    doc_a: string;
    doc_b: string;
    conflict_point?: string;
    reasoning_chain?: string;
    confidence?: number;
}

interface DocHubProps {
    doc: DocMeta;
    relatedDocs: RelatedDoc[];
    contradictions: DocContradiction[];
    notFound?: boolean;
}

const TYPE_LABEL: Record<string, string> = {
    same_topic: '同类主题',
    supplements: '补充关联',
    contradicts: '矛盾冲突',
    related_to: '相关',
};

function SectionTitle({ icon: Icon, children, tone }: {
    icon: React.ComponentType<{ size?: number; className?: string }>;
    children: React.ReactNode;
    tone?: 'danger';
}) {
    return (
        <h2 className={cn(
            'mb-3.5 flex items-center gap-1.5 text-sm font-semibold',
            tone === 'danger' ? 'text-danger-ink' : 'text-ink',
        )}>
            <Icon size={15} className={tone === 'danger' ? 'text-danger' : 'text-accent'} />
            {children}
        </h2>
    );
}

export default function DocHub({ doc, relatedDocs, contradictions, notFound }: DocHubProps) {
    if (notFound) {
        return (
            <div className="mx-auto max-w-3xl px-6 py-20 text-center">
                <span className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-subtle text-ink-3">
                    <FileSearch size={24} strokeWidth={1.75} />
                </span>
                <h1 className="text-xl font-semibold text-ink">文档不存在</h1>
                <p className="mb-6 mt-2 text-[13px] text-ink-3">
                    该文档可能已被删除或 ID 有误。
                </p>
                <Link
                    href="/wiki"
                    className="inline-flex items-center gap-1.5 text-[13px] font-medium text-accent no-underline hover:underline"
                >
                    <ArrowLeft size={14} /> 返回知识库
                </Link>
            </div>
        );
    }

    const terms = (doc.ontology_terms ?? []).filter(t => t && t.length >= 2);
    const status = doc.status ?? 'raw';
    // 矛盾里"另一方"的文档 id(去掉当前 doc)
    const otherInContradiction = (c: DocContradiction) =>
        c.doc_a === doc.id ? c.doc_b : c.doc_a;

    return (
        <div className="mx-auto max-w-4xl px-6 py-10">
            {/* 返回 */}
            <Link
                href="/wiki"
                className="mb-4 inline-flex items-center gap-1 text-xs font-medium text-ink-3 no-underline transition-colors hover:text-ink"
            >
                <ArrowLeft size={13} /> 知识库
            </Link>

            {/* Hero:标题 + 状态 + 元信息 + 摘要 */}
            <Card className="mb-4 p-6">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <h1 className="text-[22px] font-bold leading-snug tracking-tight text-ink">
                        {doc.title || doc.id}
                    </h1>
                    {doc.status && (
                        <Badge variant={STATUS_BADGE_VARIANT[status as keyof typeof STATUS_BADGE_VARIANT] ?? 'raw'}>
                            {STATUS_BADGE_LABEL[status as keyof typeof STATUS_BADGE_LABEL] ?? status}
                        </Badge>
                    )}
                </div>
                <div className="mt-2 flex flex-wrap gap-4 text-xs text-ink-3">
                    <span className="font-mono">{doc.id}</span>
                    {doc.char_count ? <span>{doc.char_count.toLocaleString('zh-CN')} 字</span> : null}
                    {doc.ingested_at ? <span>{doc.ingested_at.slice(0, 10)}</span> : null}
                </div>
                {doc.abstract_short && (
                    <p className="mt-4 border-t border-line pt-4 text-sm leading-7 text-ink-2">
                        {doc.abstract_short}
                    </p>
                )}
            </Card>

            {/* 实体面板:chip → 实体图谱页(预填 term,打通穿梭) */}
            <Card className="mb-4 p-5">
                <SectionTitle icon={Tags}>实体概念</SectionTitle>
                {terms.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                        {terms.map(term => (
                            <Link
                                key={term}
                                href={`/entity-graph?term=${encodeURIComponent(term)}&depth=2`}
                                className="rounded-full border border-accent/25 bg-accent-soft px-3 py-1 text-xs font-medium text-accent-ink no-underline transition-colors hover:bg-accent/15"
                            >
                                {term}
                            </Link>
                        ))}
                    </div>
                ) : (
                    <p className="text-xs text-ink-3">暂无实体概念</p>
                )}
            </Card>

            {/* 关联文档面板 */}
            <Card className="mb-4 p-5">
                <SectionTitle icon={Link2}>关联文档</SectionTitle>
                {relatedDocs.length > 0 ? (
                    <div className="flex flex-col gap-2">
                        {relatedDocs.map(rd => (
                            <Link
                                key={rd.doc_id}
                                href={`/wiki/${rd.doc_id}`}
                                className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2.5 no-underline transition-colors hover:border-line-strong hover:bg-hover"
                            >
                                <FileText size={14} className="shrink-0 text-ink-3" />
                                <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">
                                    {rd.title || rd.doc_id}
                                </span>
                                <span className="shrink-0 rounded-full bg-subtle px-2 py-0.5 font-mono text-[10px] text-ink-2">
                                    {TYPE_LABEL[rd.type] || rd.type}
                                    {rd.confidence ? ` · ${(rd.confidence * 100).toFixed(0)}%` : ''}
                                </span>
                            </Link>
                        ))}
                    </div>
                ) : (
                    <p className="text-xs text-ink-3">暂无关联文档</p>
                )}
            </Card>

            {/* 矛盾面板 */}
            {contradictions.length > 0 && (
                <Card className="border-danger/25 p-5">
                    <SectionTitle icon={AlertTriangle} tone="danger">涉及的矛盾</SectionTitle>
                    <div className="flex flex-col gap-2.5">
                        {contradictions.map((c, i) => {
                            const other = otherInContradiction(c);
                            return (
                                <div
                                    key={i}
                                    className="rounded-lg border border-danger/20 bg-danger-soft px-4 py-3"
                                >
                                    <p className="mb-1 text-[13px] text-ink">
                                        与{' '}
                                        <Link href={`/wiki/${other}`} className="font-mono text-xs text-accent hover:underline">
                                            {other}
                                        </Link>{' '}
                                        冲突
                                        {c.confidence != null && (
                                            <span className="text-ink-3"> · 置信度 {(c.confidence * 100).toFixed(0)}%</span>
                                        )}
                                    </p>
                                    <p className="mb-1 text-xs text-warning-ink">
                                        冲突点: {c.conflict_point || '未指明'}
                                    </p>
                                    {c.reasoning_chain && (
                                        <p className="text-xs leading-6 text-ink-2">
                                            推理链: {c.reasoning_chain}
                                        </p>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </Card>
            )}
        </div>
    );
}
