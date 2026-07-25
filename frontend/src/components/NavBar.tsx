'use client';
import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
    Anchor, BookOpen, Search, Network, ListTree, Share2, ShieldCheck, MessageSquareText, Upload,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
    { href: '/wiki', label: '知识库', icon: BookOpen },
    { href: '/search', label: '检索', icon: Search },
    { href: '/graph', label: '知识图谱', icon: Network },
    { href: '/ontology', label: '本体', icon: ListTree },
    { href: '/entity-graph', label: '实体', icon: Share2 },
    { href: '/consistency', label: '稽核', icon: ShieldCheck },
    { href: '/qa', label: '问答', icon: MessageSquareText },
    { href: '/upload', label: '上传', icon: Upload },
] as const;

export default function NavBar() {
    const pathname = usePathname();

    return (
        <header className="nav-glass fixed inset-x-0 top-0 z-[200] h-14">
            <nav className="mx-auto flex h-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
                {/* 品牌 lockup:港口域 Anchor 标识 + 统一品牌名(消除 KnowledgeBase/PortGPT 漂移) */}
                <Link href="/wiki" className="flex shrink-0 items-center gap-2 no-underline">
                    <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-white shadow-xs">
                        <Anchor size={15} strokeWidth={2.5} />
                    </span>
                    <span className="hidden text-[16px] font-bold tracking-tight text-ink xs:inline sm:inline">
                        智能知识库
                    </span>
                    <span className="font-mono text-[10px] font-medium text-ink-3">港口</span>
                </Link>

                {/* 导航链接:窄屏仅图标,md+ 图标+文字 */}
                <div className="flex items-center gap-0.5 overflow-x-auto">
                    {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
                        const isActive = pathname === href || pathname.startsWith(href + '/');
                        return (
                            <Link
                                key={href}
                                href={href}
                                title={label}
                                aria-current={isActive ? 'page' : undefined}
                                className={cn(
                                    'flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium transition-colors sm:px-3',
                                    isActive
                                        ? 'bg-accent-soft text-accent-ink'
                                        : 'text-ink-2 hover:bg-hover hover:text-ink',
                                )}
                            >
                                <Icon size={16} strokeWidth={2} />
                                <span className="hidden md:inline">{label}</span>
                            </Link>
                        );
                    })}
                </div>
            </nav>
        </header>
    );
}
