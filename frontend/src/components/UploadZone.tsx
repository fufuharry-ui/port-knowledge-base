'use client';
import React, { useRef, useState, useCallback } from 'react';
import Link from 'next/link';
import { UploadCloud, CheckCircle2, XCircle, ArrowRight } from 'lucide-react';
import { Spinner } from '@/components/ui/Spinner';
import type { UploadResult } from '@/lib/api';
import { cn } from '@/lib/utils';

interface UploadItem {
    file: File;
    state: 'uploading' | 'done' | 'error';
    result?: UploadResult;
    error?: string;
}

interface UploadZoneProps {
    onUpload: (file: File) => Promise<UploadResult>;
}

const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.doc', '.md', '.markdown', '.txt', '.html'];
const ACCEPTED_MIME = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword', 'text/markdown', 'text/plain', 'text/html'];

function isFileAccepted(file: File): boolean {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    return ACCEPTED_EXTENSIONS.includes(ext) || ACCEPTED_MIME.includes(file.type);
}

export default function UploadZone({ onUpload }: UploadZoneProps) {
    const [isDragging, setIsDragging] = useState(false);
    const [items, setItems] = useState<UploadItem[]>([]);
    const [typeError, setTypeError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const processFile = useCallback(async (file: File) => {
        setTypeError(null);
        if (!isFileAccepted(file)) {
            setTypeError(`不支持 ".${file.name.split('.').pop()}" 格式。支持: PDF / DOCX / MD / TXT`);
            return;
        }
        const item: UploadItem = { file, state: 'uploading' };
        setItems(prev => [item, ...prev]);
        try {
            const result = await onUpload(file);
            setItems(prev => prev.map(i => i.file === file ? { ...i, state: 'done', result } : i));
        } catch (err) {
            setItems(prev => prev.map(i => i.file === file ? { ...i, state: 'error', error: (err as Error).message } : i));
        }
    }, [onUpload]);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        Array.from(e.dataTransfer.files).forEach(processFile);
    }, [processFile]);

    const isUploading = items.some(i => i.state === 'uploading');

    return (
        <div className="flex flex-col gap-4">
            {/* 拖放区(键盘可达) */}
            <div
                data-testid="dropzone"
                role="button"
                tabIndex={0}
                aria-label="上传文件:拖入文件,或按回车选择文件"
                onDrop={handleDrop}
                onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
                onDragEnter={() => setIsDragging(true)}
                onDragLeave={() => setIsDragging(false)}
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        fileInputRef.current?.click();
                    }
                }}
                className={cn(
                    'dropzone-base flex flex-col items-center justify-center gap-4 px-6 py-14',
                    isDragging && 'drag-over',
                )}
            >
                <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept={ACCEPTED_EXTENSIONS.join(',')}
                    onChange={e => {
                        Array.from(e.target.files ?? []).forEach(processFile);
                        if (fileInputRef.current) fileInputRef.current.value = '';
                    }}
                    className="hidden"
                    aria-hidden="true"
                    tabIndex={-1}
                />

                {isUploading ? (
                    <Spinner size={36} data-testid="uploading-spinner" />
                ) : (
                    <span className="flex h-14 w-14 items-center justify-center rounded-full bg-accent-soft text-accent">
                        <UploadCloud size={26} strokeWidth={1.75} />
                    </span>
                )}

                <div className="text-center">
                    <p className="text-[15px] font-medium text-ink">
                        拖拽文件至此,或<span className="text-accent">点击上传</span>
                    </p>
                    <p data-testid="accepted-types" className="mt-1.5 text-xs text-ink-3">
                        支持格式：PDF · DOCX · MD · TXT · HTML
                    </p>
                </div>
            </div>

            {/* 类型错误 */}
            {typeError && (
                <div
                    data-testid="upload-error"
                    role="alert"
                    className="flex items-center gap-2 rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-[13px] font-medium text-danger-ink"
                >
                    <XCircle size={16} className="shrink-0" />
                    {typeError}
                </div>
            )}

            {/* 上传条目 */}
            {items.length > 0 && (
                <div className="flex flex-col gap-2">
                    {items.map((item, i) => (
                        <div
                            key={`${item.file.name}-${item.file.lastModified}`}
                            data-testid={item.result?.doc_id ? `upload-item-${item.result.doc_id}` : `upload-item-${i}`}
                            className={cn(
                                'flex items-center gap-3 rounded-lg border px-4 py-3',
                                item.state === 'done' && 'upload-item-done',
                                item.state === 'error' && 'upload-item-error',
                                item.state === 'uploading' && 'upload-item-uploading',
                            )}
                        >
                            {item.state === 'uploading' && <Spinner size={14} className="shrink-0" />}
                            {item.state === 'done' && <CheckCircle2 size={16} className="shrink-0 text-success" />}
                            {item.state === 'error' && <XCircle size={16} className="shrink-0 text-danger" />}

                            <div className="min-w-0 flex-1">
                                <p className="truncate text-[13px] font-medium text-ink">{item.file.name}</p>
                                {item.result?.doc_id && (
                                    <p className="mt-0.5 font-mono text-[10px] text-ink-3">{item.result.doc_id}</p>
                                )}
                                {item.state === 'done' && !item.result?.skipped && (
                                    <p className="mt-0.5 text-[11px] text-success-ink">
                                        摄入成功，后台编译中…
                                        <Link href="/wiki" className="ml-2 inline-flex items-center gap-0.5 font-medium text-accent hover:underline">
                                            去仪表盘看编译进度 <ArrowRight size={11} />
                                        </Link>
                                    </p>
                                )}
                                {item.result?.skipped && (
                                    <p className="mt-0.5 text-[11px] font-medium text-warning-ink">文件已存在，已跳过</p>
                                )}
                                {item.error && <p className="mt-0.5 text-[11px] text-danger-ink">{item.error}</p>}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
