import React from 'react';

/** 键盘快捷键标记 */
export function Kbd({ children }: { children: React.ReactNode }) {
    return (
        <kbd className="inline-flex h-5 items-center rounded border border-line bg-subtle px-1.5 font-mono text-[10px] font-medium text-ink-2 shadow-xs">
            {children}
        </kbd>
    );
}
