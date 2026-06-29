'use client';
import React from 'react';
import UploadZone from '@/components/UploadZone';
import { uploadFile } from '@/lib/api';

const STEPS = [
    { step: '1', title: '文件摄入', desc: 'SHA-256 去重，纯文本提取', icon: '📥' },
    { step: '2', title: 'LLM 编译', desc: '后台生成摘要与本体结构', icon: '🧠' },
    { step: '3', title: '关系检测', desc: '跨文档语义关联自动发现', icon: '🔗' },
];

export default function UploadPage() {
    return (
        <div style={{ maxWidth: '680px', margin: '0 auto', padding: '40px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '40px' }}>
                <span style={{ fontSize: '28px' }}>⬆️</span>
                <div>
                    <h1 style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>
                        上传文档
                    </h1>
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                        摄入 · 后台自动编译 · 语义本体提取
                    </p>
                </div>
            </div>

            <UploadZone onUpload={uploadFile} />

            {/* Steps */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginTop: '40px' }}>
                {STEPS.map(({ step, title, desc, icon }) => (
                    <div key={step} className="glass-card" style={{ textAlign: 'center', padding: '20px 16px' }}>
                        <div style={{
                            width: '36px', height: '36px', borderRadius: '50%',
                            background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.25)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            margin: '0 auto 10px', fontSize: '16px',
                        }}>
                            {icon}
                        </div>
                        <p style={{ margin: '0 0 4px', fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</p>
                        <p style={{ margin: 0, fontSize: '11px', color: 'var(--text-muted)' }}>{desc}</p>
                    </div>
                ))}
            </div>
        </div>
    );
}
