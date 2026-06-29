/**
 * tests/e2e/upload.spec.ts — 文件上传页 E2E 测试
 */
import { test, expect, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import os from 'os';

const mockUploadResponse = {
    doc_id: 'doc_20260405_001',
    title: 'test.md',
    status: 'raw',
};

async function mockUploadApi(page: Page) {
    await page.route('**/api/v1/upload', (route) =>
        route.fulfill({ json: mockUploadResponse })
    );
    await page.route('**/api/v1/docs/**', (route) =>
        route.fulfill({ json: { id: 'doc_20260405_001', status: 'compiling' } })
    );
}

test.describe('Upload Page', () => {
    test.beforeEach(async ({ page }) => {
        await mockUploadApi(page);
    });

    test('loads upload page with dropzone visible', async ({ page }) => {
        await page.goto('/upload');
        await expect(page.locator('[data-testid="dropzone"]')).toBeVisible();
    });

    test('shows accepted file types hint', async ({ page }) => {
        await page.goto('/upload');
        await expect(page.locator('[data-testid="accepted-types"]')).toBeVisible();
        await expect(page.locator('[data-testid="accepted-types"]')).toContainText(/pdf|md|txt/i);
    });

    test('uploading a valid file shows upload progress', async ({ page }) => {
        await page.goto('/upload');

        // Create a temp test file
        const tmpFile = path.join(os.tmpdir(), 'kbase_test.md');
        fs.writeFileSync(tmpFile, '# Test Document\n\n测试文档内容。');

        const fileInput = page.locator('input[type="file"]');
        await fileInput.setInputFiles(tmpFile);

        await expect(
            page.locator('[data-testid="upload-item-doc_20260405_001"]')
        ).toBeVisible({ timeout: 8000 });

        fs.unlinkSync(tmpFile);
    });

    test('shows heading on upload page', async ({ page }) => {
        await page.goto('/upload');
        await expect(page.locator('h1')).toBeVisible();
    });

    test('shows compiling status after upload', async ({ page }) => {
        await page.goto('/upload');

        const tmpFile = path.join(os.tmpdir(), 'kbase_test2.md');
        fs.writeFileSync(tmpFile, '# Another Test\n\n内容。');

        const fileInput = page.locator('input[type="file"]');
        await fileInput.setInputFiles(tmpFile);

        // After upload, should show status
        await expect(
            page.locator('[data-testid^="upload-item-"]')
        ).toBeVisible({ timeout: 8000 });

        fs.unlinkSync(tmpFile);
    });
});
