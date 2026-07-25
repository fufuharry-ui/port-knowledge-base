import Link from 'next/link';
import {
    Anchor, ArrowRight, BrainCircuit, MessageSquareText, Network, ShieldCheck, Upload,
} from 'lucide-react';

const FEATURES = [
    {
        href: '/wiki',
        icon: BrainCircuit,
        title: '文档编译',
        desc: '摄入即编译:高密度 YAML 摘要 + 语义本体,文档变成可推理的知识单元',
    },
    {
        href: '/qa',
        icon: MessageSquareText,
        title: '智能问答',
        desc: '三层渐进式 Context Stuffing 检索,回答带完整推理轨迹与引用溯源',
    },
    {
        href: '/graph',
        icon: Network,
        title: '语义图谱',
        desc: '自动发现跨文档关联,知识以图谱形态组织、可穿梭探索',
    },
    {
        href: '/consistency',
        icon: ShieldCheck,
        title: '一致性稽核',
        desc: '孤儿页、缺失概念、跨文档矛盾自动检测,守护知识库健康',
    },
] as const;

export default function HomePage() {
    return (
        <div className="mx-auto max-w-6xl px-6">
            {/* ── Hero ── */}
            <section className="flex flex-col items-center pb-16 pt-20 text-center sm:pt-28">
                <span className="mb-6 inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-ink-2 shadow-xs">
                    <Anchor size={12} className="text-accent" />
                    港口智慧化 · 知识工程
                </span>
                <h1 className="max-w-3xl text-4xl font-bold leading-[1.2] tracking-tight text-ink sm:text-5xl">
                    把港口文档,编译成
                    <span className="gradient-text">可问答的知识</span>
                </h1>
                <p className="mt-5 max-w-2xl text-[15px] leading-7 text-ink-2">
                    零向量数据库的 Karpathy 式知识库 —— 文档在摄入时编译为高密度知识工件,
                    检索时渐进式填充上下文,每一次回答都有出处。
                </p>
                <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                    <Link
                        href="/wiki"
                        className="inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white no-underline shadow-xs transition-colors hover:bg-accent-hover"
                    >
                        进入知识库
                        <ArrowRight size={15} />
                    </Link>
                    <Link
                        href="/upload"
                        className="inline-flex items-center gap-2 rounded-lg border border-line-strong bg-surface px-5 py-2.5 text-sm font-medium text-ink no-underline shadow-xs transition-colors hover:bg-hover"
                    >
                        <Upload size={15} />
                        上传文档
                    </Link>
                    <Link
                        href="/qa"
                        className="inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium text-ink-2 no-underline transition-colors hover:bg-hover hover:text-ink"
                    >
                        直接提问 →
                    </Link>
                </div>
            </section>

            {/* ── 能力矩阵 ── */}
            <section className="grid grid-cols-1 gap-4 pb-20 sm:grid-cols-2 lg:grid-cols-4">
                {FEATURES.map(({ href, icon: Icon, title, desc }) => (
                    <Link
                        key={href}
                        href={href}
                        className="group rounded-xl border border-line bg-surface p-5 no-underline shadow-card transition-all hover:-translate-y-0.5 hover:border-line-strong hover:shadow-md"
                    >
                        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-soft text-accent">
                            <Icon size={19} strokeWidth={2} />
                        </span>
                        <h2 className="mt-4 text-[15px] font-semibold text-ink">{title}</h2>
                        <p className="mt-1.5 text-[13px] leading-6 text-ink-2">{desc}</p>
                        <span className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-accent opacity-0 transition-opacity group-hover:opacity-100">
                            进入 <ArrowRight size={12} />
                        </span>
                    </Link>
                ))}
            </section>
        </div>
    );
}
