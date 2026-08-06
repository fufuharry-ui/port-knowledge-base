'use client';
import React, { useState } from 'react';
import { RefreshCw, Trash2 } from 'lucide-react';
import type { DocMeta } from '@/lib/api';
import { getCompileErrorMessage } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge, STATUS_BADGE_VARIANT, STATUS_BADGE_LABEL } from '@/components/ui/Badge';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

interface WikiCardProps {
    doc: DocMeta;
    onExpand?: (docId: string) => void;
    onDelete?: (docId: string) => void;
    onRecompile?: (docId: string) => void;
}

export default function WikiCard({ doc, onExpand, onDelete, onRecompile }: WikiCardProps) {
    const status = doc.status ?? 'raw';
    const isCompiling = status === 'compiling';
    const isError = status === 'error';
    // Loop #10 + big-loop#2:重编译对 error(重试)与 compiled(刷新)开放
    const showRecompile = (isError || status === 'compiled') && Boolean(onRecompile);
    // 删除确认:自定义 ConfirmDialog(替代 window.confirm,见 Phase 3)
    const [confirmOpen, setConfirmOpen] = useState(false);

    const stop = (e: React.MouseEvent) => e.stopPropagation();

    return (
        <Card
            variant="interactive"
            data-testid="wiki-card"
            role="button"
            tabIndex={0}
            onClick={() => onExpand?.(doc.id)}
            onKeyDown={(e) => e.key === 'Enter' && onExpand?.(doc.id)}
            aria-label={`打开文档 ${doc.title ?? doc.id}`}
            className="flex flex-col gap-2.5 p-4"
        >
            {/* 状态徽标(设计化 Badge,替代 ASCII 状态符) */}
            <Badge
                variant={STATUS_BADGE_VARIANT[status as keyof typeof STATUS_BADGE_VARIANT] ?? 'raw'}
                data-testid="status-badge"
            >
                {isCompiling && (
                    <span
                        data-testid="compiling-spinner"
                        className="spin inline-block h-2 w-2 rounded-full border border-current border-t-transparent"
                    />
                )}
                {STATUS_BADGE_LABEL[status as keyof typeof STATUS_BADGE_LABEL] ?? status}
            </Badge>

            {/* 标题 */}
            <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug text-ink">
                {doc.title ?? doc.id}
            </h3>

            {/* 摘要 */}
            {doc.abstract_short && (
                <p className="line-clamp-3 text-[13px] leading-6 text-ink-2">{doc.abstract_short}</p>
            )}

            {/* 编译失败原因:固定安全文案,不渲染后端原始 detail */}
            {isError && (
                <p
                    data-testid="compile-error-message"
                    role="status"
                    className="rounded-md bg-danger-soft px-2.5 py-2 text-[12px] leading-5 text-danger-ink"
                >
                    {getCompileErrorMessage(doc.error_code)}
                </p>
            )}

            {/* 页脚:元信息 + 管理操作 */}
            <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-3">
                <div className="flex items-center gap-3 text-[11px] text-ink-3">
                    {Boolean(doc.char_count) && (
                        <span data-testid="char-count" className="font-mono">
                            {doc.char_count!.toLocaleString('zh-CN')} 字
                        </span>
                    )}
                    {doc.ingested_at && (
                        <span>{new Date(doc.ingested_at).toLocaleDateString('zh-CN')}</span>
                    )}
                </div>
                {(onDelete || onRecompile) && (
                    <div data-testid="card-actions" className="flex items-center gap-1">
                        {showRecompile && (
                            <button
                                type="button"
                                data-testid="recompile-btn"
                                onClick={(e) => { stop(e); onRecompile?.(doc.id); }}
                                title={isError ? '重编译(error 重试)' : '重编译(刷新摘要)'}
                                aria-label="重编译"
                                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-hover hover:text-accent"
                            >
                                <RefreshCw size={14} />
                            </button>
                        )}
                        {onDelete && (
                            <button
                                type="button"
                                data-testid="delete-btn"
                                onClick={(e) => {
                                    stop(e);
                                    setConfirmOpen(true);
                                }}
                                title="删除文档"
                                aria-label="删除文档"
                                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-danger-soft hover:text-danger"
                            >
                                <Trash2 size={14} />
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* 删除确认对话框(portal 挂载,点击不冒泡到卡片) */}
            {onDelete && (
                <ConfirmDialog
                    open={confirmOpen}
                    onOpenChange={setConfirmOpen}
                    title="删除文档"
                    description={`确认删除《${doc.title ?? doc.id}》?此操作不可撤销,将移除其全部产物与引用。`}
                    confirmLabel="确认删除"
                    danger
                    onConfirm={() => onDelete(doc.id)}
                />
            )}
        </Card>
    );
}
