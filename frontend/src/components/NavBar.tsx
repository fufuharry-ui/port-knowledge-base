'use client';
import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const NAV_ITEMS = [
    { href: '/wiki', label: '知识库', icon: '📚' },
    { href: '/search', label: '检索', icon: '🔍' },
    { href: '/graph', label: '知识图谱', icon: '🕸️' },
    { href: '/ontology', label: '本体', icon: '🌳' },
    { href: '/entity-graph', label: '实体', icon: '🔗' },
    { href: '/consistency', label: '稽核', icon: '🛡️' },
    { href: '/qa', label: '问答', icon: '🧠' },
    { href: '/upload', label: '上传', icon: '⬆️' },
] as const;

export default function NavBar() {
    const pathname = usePathname();

    return (
        <header className="nav-glass" style={{
            position: 'fixed', top: 0, left: 0, right: 0, zIndex: 200,
            height: '56px'
        }}>
            <nav style={{
                maxWidth: '1100px', margin: '0 auto',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '0 24px', height: '100%'
            }}>
                {/* Logo */}
                <Link href="/wiki" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span className="gradient-text" style={{ fontSize: '17px', fontWeight: 700, letterSpacing: '-0.3px' }}>
                        KnowledgeBase
                    </span>
                    <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>v2.0</span>
                </Link>

                {/* Nav links */}
                <div style={{ display: 'flex', gap: '4px' }}>
                    {NAV_ITEMS.map(({ href, label, icon }) => {
                        const isActive = pathname === href || pathname.startsWith(href + '/');
                        return (
                            <Link key={href} href={href} style={{
                                textDecoration: 'none',
                                display: 'flex', alignItems: 'center', gap: '5px',
                                padding: '6px 14px', borderRadius: '10px',
                                fontSize: '13px', fontWeight: 500,
                                transition: 'all 0.15s',
                                background: isActive ? 'rgba(255,255,255,0.10)' : 'transparent',
                                color: isActive ? '#f0f0f5' : 'rgba(240,240,245,0.45)',
                                border: isActive ? '1px solid rgba(255,255,255,0.12)' : '1px solid transparent',
                            }}>
                                <span>{icon}</span>
                                {label}
                            </Link>
                        );
                    })}
                </div>
            </nav>
        </header>
    );
}
