/**
 * tests/unit/SearchBox.test.tsx — SearchBox 组件 TDD 测试
 * 验证 Raycast 风格检索框的输入、提交、快捷键行为
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SearchBox from '@/components/SearchBox';

describe('SearchBox', () => {
    test('renders input with correct placeholder', () => {
        render(<SearchBox onSubmit={jest.fn()} />);
        expect(screen.getByPlaceholderText(/搜索知识库|请输入问题/i)).toBeInTheDocument();
    });

    test('calls onSubmit with query when Enter is pressed', async () => {
        const user = userEvent.setup();
        const onSubmit = jest.fn();
        render(<SearchBox onSubmit={onSubmit} />);

        const input = screen.getByRole('textbox');
        await user.type(input, '岸桥延迟要求');
        await user.keyboard('{Enter}');

        expect(onSubmit).toHaveBeenCalledWith('岸桥延迟要求');
    });

    test('does NOT call onSubmit for empty query', async () => {
        const user = userEvent.setup();
        const onSubmit = jest.fn();
        render(<SearchBox onSubmit={onSubmit} />);

        const input = screen.getByRole('textbox');
        await user.click(input);
        await user.keyboard('{Enter}');

        expect(onSubmit).not.toHaveBeenCalled();
    });

    test('does NOT call onSubmit for whitespace-only query', async () => {
        const user = userEvent.setup();
        const onSubmit = jest.fn();
        render(<SearchBox onSubmit={onSubmit} />);

        const input = screen.getByRole('textbox');
        await user.type(input, '   ');
        await user.keyboard('{Enter}');

        expect(onSubmit).not.toHaveBeenCalled();
    });

    test('Ctrl+K focuses the input', async () => {
        const user = userEvent.setup();
        const onSubmit = jest.fn();
        render(<SearchBox onSubmit={onSubmit} />);

        // Initially not focused
        const input = screen.getByRole('textbox');
        expect(input).not.toHaveFocus();

        // Trigger Ctrl+K
        fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
        expect(input).toHaveFocus();
    });

    test('shows loading state while isLoading=true', () => {
        render(<SearchBox onSubmit={jest.fn()} isLoading={true} />);
        expect(screen.getByTestId('search-spinner')).toBeInTheDocument();
    });

    test('disables input while loading', () => {
        render(<SearchBox onSubmit={jest.fn()} isLoading={true} />);
        expect(screen.getByRole('textbox')).toBeDisabled();
    });

    test('renders submit button that triggers onSubmit', async () => {
        const user = userEvent.setup();
        const onSubmit = jest.fn();
        render(<SearchBox onSubmit={onSubmit} />);

        const input = screen.getByRole('textbox');
        await user.type(input, '港口安全');

        const submitBtn = screen.getByRole('button');
        await user.click(submitBtn);
        expect(onSubmit).toHaveBeenCalledWith('港口安全');
    });
});
