/**
 * tests/unit/WikiCard.actions.test.tsx — 文档管理操作按钮测试 (Loop #10)
 * 验证删除/重编译按钮渲染 + stopPropagation(不误触展开)+ 删除需 confirm
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

    test('点删除按钮触发 confirm,确认后才调 onDelete', () => {
        const confirmSpy = jest.spyOn(window, 'confirm').mockReturnValue(true);
        const onDelete = jest.fn();
        render(<WikiCard doc={baseDoc} onDelete={onDelete} />);
        fireEvent.click(screen.getByTestId('delete-btn'));
        expect(confirmSpy).toHaveBeenCalled();
        expect(onDelete).toHaveBeenCalledWith('doc_1');
        confirmSpy.mockRestore();
    });

    test('点删除按钮 confirm 取消时不调 onDelete', () => {
        const confirmSpy = jest.spyOn(window, 'confirm').mockReturnValue(false);
        const onDelete = jest.fn();
        render(<WikiCard doc={baseDoc} onDelete={onDelete} />);
        fireEvent.click(screen.getByTestId('delete-btn'));
        expect(onDelete).not.toHaveBeenCalled();
        confirmSpy.mockRestore();
    });

    test('点删除按钮不触发卡片展开(stopPropagation)', () => {
        jest.spyOn(window, 'confirm').mockReturnValue(true);
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

    test('非 error 状态不渲染重编译按钮', () => {
        const onRecompile = jest.fn();
        render(<WikiCard doc={baseDoc} onRecompile={onRecompile} />);
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
