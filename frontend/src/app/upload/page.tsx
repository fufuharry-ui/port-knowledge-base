'use client';
import React from 'react';
import { Upload, FileDown, BrainCircuit, Share2 } from 'lucide-react';
import UploadZone from '@/components/UploadZone';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { uploadFile } from '@/lib/api';

const STEPS = [
    { step: '1', title: '文件摄入', desc: 'SHA-256 去重，纯文本提取', icon: FileDown },
    { step: '2', title: 'LLM 编译', desc: '后台生成摘要与本体结构', icon: BrainCircuit },
    { step: '3', title: '关系检测', desc: '跨文档语义关联自动发现', icon: Share2 },
];

export default function UploadPage() {
    return (
        <div className="mx-auto max-w-3xl px-6 py-10">
            <PageHeader
                icon={Upload}
                title="上传文档"
                subtitle="摄入 · 后台自动编译 · 语义本体提取"
                className="mb-8"
            />

            <UploadZone onUpload={uploadFile} />

            {/* 处理流程 */}
            <div className="mt-10 grid grid-cols-1 gap-3 sm:grid-cols-3">
                {STEPS.map(({ step, title, desc, icon: Icon }) => (
                    <Card key={step} className="p-5 text-center">
                        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-accent-soft text-accent">
                            <Icon size={18} strokeWidth={2} />
                        </div>
                        <p className="text-[13px] font-semibold text-ink">{title}</p>
                        <p className="mt-1 text-xs leading-5 text-ink-3">{desc}</p>
                    </Card>
                ))}
            </div>
        </div>
    );
}
