/**
 * tests/unit/UploadZone.test.tsx — UploadZone 组件 TDD 测试
 * 验证拖拽上传区域的渲染、文件类型验证、上传状态
 */
import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UploadZone from '@/components/UploadZone';

describe('UploadZone', () => {
    test('renders dropzone instruction text', () => {
        render(<UploadZone onUpload={jest.fn()} />);
        expect(screen.getByTestId('dropzone')).toBeInTheDocument();
        const matches = screen.getAllByText(/拖拽文件|点击上传|drag/i);
        expect(matches.length).toBeGreaterThan(0);
    });

    test('shows accepted file types hint', () => {
        render(<UploadZone onUpload={jest.fn()} />);
        expect(screen.getByTestId('accepted-types')).toHaveTextContent(/pdf|md|txt|docx/i);
    });

    test('calls onUpload with file when valid file is dropped', async () => {
        const onUpload = jest.fn().mockResolvedValue({ doc_id: 'doc_001', status: 'raw' });
        render(<UploadZone onUpload={onUpload} />);

        const file = new File(['# Hello'], 'test.md', { type: 'text/markdown' });
        const dropzone = screen.getByTestId('dropzone');

        fireEvent.drop(dropzone, {
            dataTransfer: { files: [file] },
        });

        await waitFor(() => {
            expect(onUpload).toHaveBeenCalledWith(file);
        });
    });

    test('shows error for invalid file type', async () => {
        const onUpload = jest.fn();
        render(<UploadZone onUpload={onUpload} />);

        const file = new File(['binary'], 'virus.exe', { type: 'application/octet-stream' });
        const dropzone = screen.getByTestId('dropzone');

        fireEvent.drop(dropzone, {
            dataTransfer: { files: [file] },
        });

        await waitFor(() => {
            expect(screen.getByTestId('upload-error')).toBeInTheDocument();
        });
        expect(onUpload).not.toHaveBeenCalled();
    });

    test('accepts .pdf file', async () => {
        const onUpload = jest.fn().mockResolvedValue({ doc_id: 'doc_002', status: 'raw' });
        render(<UploadZone onUpload={onUpload} />);

        const file = new File(['%PDF'], 'report.pdf', { type: 'application/pdf' });
        const dropzone = screen.getByTestId('dropzone');

        fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

        await waitFor(() => {
            expect(onUpload).toHaveBeenCalledWith(file);
        });
    });

    test('shows upload progress with doc_id after successful upload', async () => {
        const onUpload = jest.fn().mockResolvedValue({ doc_id: 'doc_001', status: 'raw', title: 'test.md' });
        render(<UploadZone onUpload={onUpload} />);

        const file = new File(['# Test'], 'test.md', { type: 'text/markdown' });
        const dropzone = screen.getByTestId('dropzone');

        fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

        await waitFor(() => {
            const successItem = screen.getByTestId('upload-item-doc_001');
            expect(successItem).toBeInTheDocument();
        });
    });

    test('shows uploading spinner while uploading', async () => {
        // Simulate slow upload
        const onUpload = jest.fn().mockReturnValue(new Promise(() => { }));
        render(<UploadZone onUpload={onUpload} />);

        const file = new File(['# Test'], 'slow.md', { type: 'text/markdown' });
        const dropzone = screen.getByTestId('dropzone');

        fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

        await waitFor(() => {
            expect(screen.getByTestId('uploading-spinner')).toBeInTheDocument();
        });
    });

    test('shows drag-over visual feedback on dragenter', () => {
        render(<UploadZone onUpload={jest.fn()} />);
        const dropzone = screen.getByTestId('dropzone');

        fireEvent.dragEnter(dropzone);
        expect(dropzone.className).toMatch(/drag-over|dragover|border-blue|active/i);
    });
});
