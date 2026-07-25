/**
 * tests/unit/WikiCard.actions.test.tsx — 文档管理操作按钮测试 (Loop #10)
 * 验证删除/重编译按钮渲染 + stopPropagation(不误触展开)+ 删除需确认对话框
 * (Phase 3:window.confirm → radix ConfirmDialog)
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import WikiCard from '@/components/WikiCard';
import type { DocMeta } from '@/lib/api';

const baseDoc: DocMeta = { id: 'doc_1', title: '测试文档', status: 'compiled', char_count: 100 };

describe('WikiCard 管理操作 (Loop #10)', () => {
    test('传入 onDelete 时渲染删除按钮', () => {
        render(<WikiCard doc={baseDoc} onDelete={jest.fn()} />);
        expect(screen.getByTestId('delete-btn')).toBeInTheDocument();
    });

    test('点删除按钮弹出确认对话框,确认后才调 onDelete', () => {
        const onDelete = jest.fn();
        render(<WikiCard doc={baseDoc} onDelete={onDelete} />);
        fireEvent.click(screen.getByTestId('delete-btn'));
        // 未确认前不调 onDelete
        expect(onDelete).not.toHaveBeenCalled();
        expect(screen.getByTestId('confirm-dialog')).toBeInTheDocument();
        fireEvent.click(screen.getByTestId('confirm-accept'));
        expect(onDelete).toHaveBeenCalledWith('doc_1');
    });

    test('确认对话框取消时不调 onDelete', () => {
        const onDelete = jest.fn();
        render(<WikiCard doc={baseDoc} onDelete={onDelete} />);
        fireEvent.click(screen.getByTestId('delete-btn'));
        fireEvent.click(screen.getByTestId('confirm-cancel'));
        expect(onDelete).not.toHaveBeenCalled();
    });

    test('点删除按钮不触发卡片展开(stopPropagation)', () => {
        const onExpand = jest.fn();
        const onDelete = jest.fn();
        render(<WikiCard doc={baseDoc} onExpand={onExpand} onDelete={onDelete} />);
        fireEvent.click(screen.getByTestId('delete-btn'));
        expect(onExpand).not.toHaveBeenCalled();
    });

    test('error 状态文档渲染重编译按钮', () => {
        const onRecompile = jest.fn();
        render(<WikiCard doc={{ ...baseDoc, status: 'error' }} onRecompile={onRecompile} />);
        expect(screen.getByTestId('recompile-btn')).toBeInTheDocument();
    });

    test('compiled 状态渲染重编译按钮(big-loop #1 扩展:刷新摘要)', () => {
        const onRecompile = jest.fn();
        render(<WikiCard doc={baseDoc} onRecompile={onRecompile} />);
        expect(screen.getByTestId('recompile-btn')).toBeInTheDocument();
    });

    test('raw/compiling 状态不渲染重编译按钮', () => {
        const onRecompile = jest.fn();
        const { unmount } = render(<WikiCard doc={{ ...baseDoc, status: 'raw' }} onRecompile={onRecompile} />);
        expect(screen.queryByTestId('recompile-btn')).toBeNull();
        unmount();
        render(<WikiCard doc={{ ...baseDoc, status: 'compiling' }} onRecompile={onRecompile} />);
        expect(screen.queryByTestId('recompile-btn')).toBeNull();
    });

    test('点重编译触发 onRecompile,不触发展开', () => {
        const onRecompile = jest.fn();
        const onExpand = jest.fn();
        render(<WikiCard doc={{ ...baseDoc, status: 'error' }} onExpand={onExpand} onRecompile={onRecompile} />);
        fireEvent.click(screen.getByTestId('recompile-btn'));
        expect(onRecompile).toHaveBeenCalledWith('doc_1');
        expect(onExpand).not.toHaveBeenCalled();
    });

    test('不传 onDelete/onRecompile 时不渲染操作区(向后兼容)', () => {
        render(<WikiCard doc={baseDoc} />);
        expect(screen.queryByTestId('card-actions')).toBeNull();
    });
});
