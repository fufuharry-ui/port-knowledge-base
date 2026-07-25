/**
 * /consistency 页 — 一致性稽核面板 (Big-Loop #4, Loop #3 能力页面可见)
 * 展示已知矛盾列表 + 候选对数 + "触发稽核"按钮(POST)。
 * 矛盾为 0 时诚实展示"库内一致"(ADR-14)。
 */
'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { ShieldCheck, RefreshCw, AlertTriangle, CheckCircle2, Clock, GitCompareArrows } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { StatTile } from '@/components/ui/StatTile';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Skeleton, SkeletonText } from '@/components/ui/Skeleton';
import {
    fetchConsistency, triggerConsistencyCheck, type ConsistencyReport,
} from '@/lib/api';

export default function ConsistencyPage() {
    const [report, setReport] = useState<ConsistencyReport | null>(null);
    const [loading, setLoading] = useState(true);
    const [checking, setChecking] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setReport(await fetchConsistency());
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const runCheck = async () => {
        setChecking(true);
        setError(null);
        try {
            const result = await triggerConsistencyCheck();
            setReport(result);
            if (result.status === 'error') {
                setError(result.message ?? '稽核失败');
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setChecking(false);
        }
    };

    const contradictions = report?.contradictions ?? [];
    const total = report?.total ?? 0;
    const candidates = report?.candidates_checked ?? 0;

    return (
        <div className="mx-auto max-w-5xl px-6 py-10">
            <PageHeader
                icon={ShieldCheck}
                title="一致性稽核"
                subtitle="跨文档矛盾检出——当多文档对同一事实有冲突论断时,提示用户甄别"
                className="mb-6"
                action={
                    <Button onClick={runCheck} disabled={checking}>
                        <RefreshCw size={15} className={checking ? 'spin' : undefined} />
                        {checking ? '稽核中…' : '触发稽核'}
                    </Button>
                }
            />

            {/* Stats */}
            <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
                <StatTile
                    icon={AlertTriangle}
                    label="检出矛盾"
                    value={total}
                    tone={total > 0 ? 'danger' : 'success'}
                />
                <StatTile icon={GitCompareArrows} label="稽核候选对" value={candidates} />
                {report?.last_updated && (
                    <StatTile
                        icon={Clock}
                        label="最后稽核"
                        value={
                            <span className="text-sm">
                                {report.last_updated.slice(0, 16).replace('T', ' ')}
                            </span>
                        }
                    />
                )}
            </div>

            {loading && (
                <Card className="p-6">
                    <Skeleton className="h-5 w-48" />
                    <SkeletonText lines={4} className="mt-4" />
                </Card>
            )}

            {error && (
                <div
                    role="alert"
                    className="mb-4 rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-[13px] text-danger-ink"
                >
                    ⚠ {error}
                </div>
            )}

            {!loading && !error && report && (
                <Card className="p-5">
                    {total === 0 ? (
                        <div data-role="consistency-clean" className="py-8 text-center">
                            <span className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-success-soft text-success">
                                <CheckCircle2 size={22} strokeWidth={2} />
                            </span>
                            <p className="text-sm font-medium text-success-ink">
                                知识库内一致,未检出矛盾
                            </p>
                            {candidates > 0 && (
                                <p className="mt-1.5 text-xs text-ink-3">
                                    (已比对 {candidates} 对候选文档)
                                </p>
                            )}
                        </div>
                    ) : (
                        <div data-role="consistency-conflicts">
                            <p className="mb-3.5 flex items-center gap-1.5 text-[13px] font-semibold text-danger-ink">
                                <AlertTriangle size={14} />
                                检出 {total} 处跨文档矛盾:
                            </p>
                            <div className="flex flex-col gap-2.5">
                                {contradictions.map((c, i) => (
                                    <div
                                        key={i}
                                        className="rounded-lg border border-danger/20 bg-danger-soft px-4 py-3"
                                    >
                                        <div className="mb-1.5 flex flex-wrap items-center gap-2">
                                            <code className="rounded-sm border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink">
                                                {c.doc_a}
                                            </code>
                                            <span className="text-ink-3">↔</span>
                                            <code className="rounded-sm border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink">
                                                {c.doc_b}
                                            </code>
                                            {c.confidence != null && (
                                                <span className="ml-auto text-[11px] text-ink-3">
                                                    置信度 {c.confidence.toFixed(2)}
                                                </span>
                                            )}
                                        </div>
                                        {c.conflict_point && (
                                            <p className="mb-1 text-[13px] text-ink">
                                                <strong className="font-semibold">冲突点:</strong> {c.conflict_point}
                                            </p>
                                        )}
                                        {c.reasoning_chain && (
                                            <p className="text-xs leading-6 text-ink-3">
                                                {c.reasoning_chain}
                                            </p>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
}
