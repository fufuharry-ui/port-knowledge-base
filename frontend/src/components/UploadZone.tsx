'use client';
import React, { useRef, useState, useCallback } from 'react';
import type { UploadResult } from '@/lib/api';

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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {/* Drop zone */}
            <div
                data-testid="dropzone"
                onDrop={handleDrop}
                onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
                onDragEnter={() => setIsDragging(true)}
                onDragLeave={() => setIsDragging(false)}
                onClick={() => fileInputRef.current?.click()}
                className={`dropzone-base${isDragging ? ' drag-over' : ''}`}
                style={{
                    display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center',
                    gap: '16px', padding: '56px 24px',
                }}
            >
                <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept={ACCEPTED_EXTENSIONS.join(',')}
                    onChange={e => { Array.from(e.target.files ?? []).forEach(processFile); if (fileInputRef.current) fileInputRef.current.value = ''; }}
                    style={{ display: 'none' }}
                />

                {isUploading ? (
                    <span data-testid="uploading-spinner" className="spin" style={{
                        display: 'inline-block', width: '40px', height: '40px',
                        border: '3px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                    }} />
                ) : (
                    <span style={{ fontSize: '36px' }}>☁️</span>
                )}

                <div style={{ textAlign: 'center' }}>
                    <p style={{ fontSize: '14px', color: 'var(--text-secondary)', margin: '0 0 4px' }}>
                        拖拽文件至此或<span style={{ color: 'var(--accent-blue)' }}>点击上传</span>
                    </p>
                    <p data-testid="accepted-types" style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        支持格式：PDF · DOCX · MD · TXT · HTML
                    </p>
                </div>
            </div>

            {/* Type error */}
            {typeError && (
                <div data-testid="upload-error" style={{
                    display: 'flex', alignItems: 'center', gap: '8px',
                    color: 'var(--accent-red)', fontSize: '13px',
                    padding: '12px 16px', borderRadius: '12px',
                    background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.20)',
                }}>
                    ⚠ {typeError}
                </div>
            )}

            {/* Upload items */}
            {items.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {items.map((item, i) => (
                        <div
                            key={`${item.file.name}-${item.file.lastModified}`}
                            data-testid={item.result?.doc_id ? `upload-item-${item.result.doc_id}` : `upload-item-${i}`}
                            className={`upload-item-${item.state}`}
                            style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '12px 16px' }}
                        >
                            {item.state === 'uploading' && (
                                <span className="spin" style={{
                                    display: 'inline-block', width: '14px', height: '14px',
                                    border: '2px solid var(--accent-blue)', borderTopColor: 'transparent', borderRadius: '50%',
                                }} />
                            )}
                            {item.state === 'done' && <span>✅</span>}
                            {item.state === 'error' && <span>❌</span>}

                            <div style={{ flex: 1, minWidth: 0 }}>
                                <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {item.file.name}
                                </p>
                                {item.result?.doc_id && (
                                    <p style={{ margin: 0, fontSize: '10px', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                                        {item.result.doc_id}
                                    </p>
                                )}
                                {item.state === 'done' && !item.result?.skipped && (
                                    <p style={{ margin: 0, fontSize: '11px', color: 'var(--accent-green)' }}>
                                        摄入成功，后台编译中…
                                        <a href="/wiki" style={{ marginLeft: '8px', color: 'var(--accent-blue)', textDecoration: 'none' }}>
                                            去仪表盘看编译进度 →
                                        </a>
                                    </p>
                                )}
                                {item.result?.skipped && (
                                    <p style={{ margin: 0, fontSize: '11px', color: 'var(--accent-amber)' }}>文件已存在，已跳过</p>
                                )}
                                {item.error && <p style={{ margin: 0, fontSize: '11px', color: 'var(--accent-red)' }}>{item.error}</p>}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
