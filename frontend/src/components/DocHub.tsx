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
import type { DocMeta } from '@/lib/api';

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

export default function DocHub({ doc, relatedDocs, contradictions, notFound }: DocHubProps) {
    if (notFound) {
        return (
            <div style={{ maxWidth: '780px', margin: '0 auto', padding: '80px 24px', textAlign: 'center' }}>
                <span style={{ fontSize: '48px' }}>🔍</span>
                <h1 style={{ fontSize: '22px', color: 'var(--text-primary)', margin: '16px 0 8px' }}>
                    文档不存在
                </h1>
                <p style={{ color: 'var(--text-muted)', fontSize: '13px', marginBottom: '24px' }}>
                    该文档可能已被删除或 ID 有误。
                </p>
                <Link href="/wiki" style={backLinkStyle}>← 返回知识库</Link>
            </div>
        );
    }

    const terms = (doc.ontology_terms ?? []).filter(t => t && t.length >= 2);
    // 矛盾里"另一方"的文档 id(去掉当前 doc)
    const otherInContradiction = (c: DocContradiction) =>
        c.doc_a === doc.id ? c.doc_b : c.doc_a;

    return (
        <div style={{ maxWidth: '900px', margin: '0 auto', padding: '40px 24px' }}>
            {/* 返回 */}
            <Link href="/wiki" style={backLinkStyle}>← 知识库</Link>

            {/* 标题区 */}
            <div style={{ marginBottom: '24px' }}>
                <h1 style={{ fontSize: '26px', fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 8px' }}>
                    {doc.title || doc.id}
                </h1>
                <div style={{ display: 'flex', gap: '16px', fontSize: '12px', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: 'monospace' }}>{doc.id}</span>
                    {doc.status && (
                        <span style={{ color: doc.status === 'compiled' ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
                            {doc.status === 'compiled' ? '✓ ' : '○ '}{doc.status}
                        </span>
                    )}
                    {doc.char_count ? <span>{doc.char_count.toLocaleString()} 字</span> : null}
                    {doc.ingested_at ? <span>{doc.ingested_at.slice(0, 10)}</span> : null}
                </div>
            </div>

            {/* 摘要 */}
            {doc.abstract_short && (
                <section style={sectionStyle}>
                    <h2 style={h2Style}>📝 摘要</h2>
                    <p style={{ fontSize: '14px', lineHeight: 1.8, color: 'var(--text-secondary)', margin: 0 }}>
                        {doc.abstract_short}
                    </p>
                </section>
            )}

            {/* 实体面板:chip → 实体图谱页(预填 term,打通穿梭) */}
            <section style={sectionStyle}>
                <h2 style={h2Style}>🏷️ 实体概念</h2>
                {terms.length > 0 ? (
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        {terms.map(term => (
                            <Link
                                key={term}
                                href={`/entity-graph?term=${encodeURIComponent(term)}&depth=2`}
                                style={chipStyle}
                            >
                                {term}
                            </Link>
                        ))}
                    </div>
                ) : (
                    <p style={emptyStyle}>暂无实体概念</p>
                )}
            </section>

            {/* 关联文档面板 */}
            <section style={sectionStyle}>
                <h2 style={h2Style}>🔗 关联文档</h2>
                {relatedDocs.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {relatedDocs.map(rd => (
                            <Link
                                key={rd.doc_id}
                                href={`/wiki/${rd.doc_id}`}
                                style={relatedCardStyle}
                            >
                                <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>
                                    {rd.title || rd.doc_id}
                                </span>
                                <span style={{ fontSize: '11px', color: 'var(--accent-blue)', fontFamily: 'monospace', marginLeft: '8px' }}>
                                    {TYPE_LABEL[rd.type] || rd.type}
                                    {rd.confidence ? ` · ${(rd.confidence * 100).toFixed(0)}%` : ''}
                                </span>
                            </Link>
                        ))}
                    </div>
                ) : (
                    <p style={emptyStyle}>暂无关联文档</p>
                )}
            </section>

            {/* 矛盾面板 */}
            {contradictions.length > 0 && (
                <section style={sectionStyle}>
                    <h2 style={{ ...h2Style, color: 'var(--accent-red)' }}>⚠️ 涉及的矛盾</h2>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                        {contradictions.map((c, i) => {
                            const other = otherInContradiction(c);
                            return (
                                <div key={i} style={contradictionStyle}>
                                    <div style={{ fontSize: '13px', color: 'var(--text-primary)', marginBottom: '4px' }}>
                                        与 <Link href={`/wiki/${other}`} style={{ color: 'var(--accent-blue)' }}>{other}</Link> 冲突
                                        {c.confidence != null && ` · 置信度 ${(c.confidence * 100).toFixed(0)}%`}
                                    </div>
                                    <div style={{ fontSize: '12px', color: 'var(--accent-amber)', marginBottom: '4px' }}>
                                        冲突点: {c.conflict_point || '未指明'}
                                    </div>
                                    {c.reasoning_chain && (
                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                                            推理链: {c.reasoning_chain}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </section>
            )}
        </div>
    );
}

const backLinkStyle: React.CSSProperties = {
    display: 'inline-block', fontSize: '12px', color: 'var(--text-muted)',
    textDecoration: 'none', marginBottom: '16px',
};
const sectionStyle: React.CSSProperties = {
    padding: '20px 24px', marginBottom: '16px', borderRadius: '14px',
    background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
};
const h2Style: React.CSSProperties = {
    fontSize: '14px', fontWeight: 600, color: 'var(--text-secondary)',
    margin: '0 0 14px', letterSpacing: '0.3px',
};
const chipStyle: React.CSSProperties = {
    display: 'inline-block', padding: '5px 12px', borderRadius: '999px',
    fontSize: '12px', color: 'var(--accent-cyan)', textDecoration: 'none',
    background: 'rgba(34,211,238,0.08)', border: '1px solid rgba(34,211,238,0.20)',
    transition: 'all 0.15s',
};
const relatedCardStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: '4px', padding: '10px 14px',
    borderRadius: '10px', textDecoration: 'none',
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
};
const emptyStyle: React.CSSProperties = {
    fontSize: '12px', color: 'var(--text-muted)', margin: 0,
};
const contradictionStyle: React.CSSProperties = {
    padding: '12px 14px', borderRadius: '10px',
    background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.18)',
};
