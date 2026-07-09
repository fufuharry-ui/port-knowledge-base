'use client';
import React from 'react';
import type { DocMeta } from '@/lib/api';

interface WikiCardProps {
    doc: DocMeta;
    onExpand?: (docId: string) => void;
    onDelete?: (docId: string) => void;
    onRecompile?: (docId: string) => void;
}

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
    compiled: { label: '✓ compiled', cls: 'badge-compiled' },
    raw: { label: '○ raw', cls: 'badge-raw' },
    compiling: { label: '↻ compiling', cls: 'badge-compiling' },
    error: { label: '✗ error', cls: 'badge-error' },
};

export default function WikiCard({ doc, onExpand, onDelete, onRecompile }: WikiCardProps) {
    const status = doc.status ?? 'raw';
    const badge = STATUS_BADGE[status] ?? STATUS_BADGE.raw;
    const isCompiling = status === 'compiling';
    const isError = status === 'error';

    const stop = (e: React.MouseEvent) => e.stopPropagation();

    return (
        <div
            data-testid="wiki-card"
            role="button"
            tabIndex={0}
            onClick={() => onExpand?.(doc.id)}
            onKeyDown={(e) => e.key === 'Enter' && onExpand?.(doc.id)}
            className="glass-card"
            style={{
                display: 'flex', flexDirection: 'column', gap: '10px',
                padding: '18px', cursor: 'pointer',
                transition: 'transform 0.18s, box-shadow 0.18s',
                outline: 'none', position: 'relative',
            }}
            onMouseEnter={e => {
                (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)';
                (e.currentTarget as HTMLElement).style.boxShadow = '0 8px 32px rgba(0,0,0,0.4)';
            }}
            onMouseLeave={e => {
                (e.currentTarget as HTMLElement).style.transform = '';
                (e.currentTarget as HTMLElement).style.boxShadow = '';
            }}
        >
            {/* Status badge */}
            <span
                data-testid="status-badge"
                className={badge.cls}
                style={{
                    display: 'inline-flex', alignItems: 'center', gap: '5px',
                    fontSize: '11px', fontWeight: 500,
                    padding: '2px 9px', borderRadius: '9999px', width: 'fit-content'
                }}
            >
                {isCompiling && (
                    <span
                        data-testid="compiling-spinner"
                        className="spin"
                        style={{
                            display: 'inline-block', width: '8px', height: '8px',
                            border: '1.5px solid currentColor', borderTopColor: 'transparent',
                            borderRadius: '50%',
                        }}
                    />
                )}
                {badge.label}
            </span>

            {/* Title */}
            <h3 style={{
                fontSize: '14px', fontWeight: 600,
                color: 'var(--text-primary)', margin: 0,
                display: '-webkit-box', WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical', overflow: 'hidden',
                lineHeight: 1.4,
            }}>
                {doc.title ?? doc.id}
            </h3>

            {/* Abstract */}
            {doc.abstract_short && (
                <p style={{
                    fontSize: '12px', color: 'var(--text-secondary)',
                    margin: 0, lineHeight: 1.6,
                    display: '-webkit-box', WebkitLineClamp: 3,
                    WebkitBoxOrient: 'vertical', overflow: 'hidden',
                }}>
                    {doc.abstract_short}
                </p>
            )}

            {/* Footer */}
            <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                marginTop: 'auto', paddingTop: '10px',
                borderTop: '1px solid var(--border-subtle)',
            }}>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                    {doc.char_count && (
                        <span data-testid="char-count" style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                            {doc.char_count.toLocaleString('zh-CN')} 字
                        </span>
                    )}
                    {doc.ingested_at && (
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                            {new Date(doc.ingested_at).toLocaleDateString('zh-CN')}
                        </span>
                    )}
                </div>
                {/* 管理操作(Loop #10):hover 显出,stopPropagation 防误触展开 */}
                {(onDelete || onRecompile) && (
                    <div data-testid="card-actions" className="card-actions" style={{
                        display: 'flex', gap: '6px', opacity: 0.6,
                    }}>
                        {isError && onRecompile && (
                            <button
                                data-testid="recompile-btn"
                                onClick={e => { stop(e); onRecompile(doc.id); }}
                                title="重编译(error 重试)"
                                style={actionBtnStyle}
                            >↻</button>
                        )}
                        {onDelete && (
                            <button
                                data-testid="delete-btn"
                                onClick={e => {
                                    stop(e);
                                    if (window.confirm(`确认删除《${doc.title ?? doc.id}》?此操作不可撤销,将移除其全部产物与引用。`)) {
                                        onDelete(doc.id);
                                    }
                                }}
                                title="删除文档"
                                style={{ ...actionBtnStyle, color: 'var(--accent-red)' }}
                            >🗑</button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

const actionBtnStyle: React.CSSProperties = {
    padding: '2px 8px', fontSize: '12px', lineHeight: 1,
    borderRadius: '6px', cursor: 'pointer',
    border: '1px solid rgba(255,255,255,0.10)',
    background: 'rgba(255,255,255,0.04)',
    color: 'var(--text-muted)',
};
