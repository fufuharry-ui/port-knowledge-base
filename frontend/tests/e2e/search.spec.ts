/**
 * tests/e2e/search.spec.ts — 检索页面 E2E 测试
 */
import { test, expect, Page } from '@playwright/test';

const mockSearchResponse = {
    answer: '根据《岸桥远控技术方案》，端到端延迟≤50ms，5G空口延迟≤20ms。',
    sources: [
        { doc_id: 'doc_001', title: '岸桥远控技术方案' },
    ],
};

async function mockSearchApi(page: Page) {
    await page.route('**/api/v1/search', (route) =>
        route.fulfill({ json: mockSearchResponse })
    );
    // SSE stream mock
    await page.route('**/api/v1/search/stream*', (route) => {
        const body = [
            'data: {"delta":"根据《岸桥远控技术方案》"}\n\n',
            'data: {"delta":"，延迟≤50ms"}\n\n',
            'data: [DONE]\n\n',
        ].join('');
        route.fulfill({
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
            body,
        });
    });
}

test.describe('Search Page', () => {
    test.beforeEach(async ({ page }) => {
        await mockSearchApi(page);
    });

    test('loads search page with visible heading', async ({ page }) => {
        await page.goto('/search');
        await expect(page.locator('h1')).toBeVisible();
    });

    test('search input is visible and focusable', async ({ page }) => {
        await page.goto('/search');
        const input = page.locator('[role="textbox"]').first();
        await expect(input).toBeVisible();
        await input.focus();
        await expect(input).toBeFocused();
    });

    test('typing query and pressing Enter shows answer', async ({ page }) => {
        await page.goto('/search');
        const input = page.locator('[role="textbox"]').first();
        await input.fill('岸桥延迟要求');
        await input.press('Enter');
        await expect(page.locator('[data-testid="answer-text"]')).toBeVisible({ timeout: 10000 });
    });

    test('answer section appears after search', async ({ page }) => {
        await page.goto('/search');
        await page.locator('[role="textbox"]').first().fill('岸桥');
        await page.locator('[role="textbox"]').first().press('Enter');
        await expect(page.locator('[data-testid="answer-text"]')).toContainText('延迟', { timeout: 10000 });
    });

    test('source badges appear in result', async ({ page }) => {
        await page.goto('/search');
        await page.locator('[role="textbox"]').first().fill('岸桥延迟');
        await page.locator('[role="textbox"]').first().press('Enter');
        await expect(page.locator('[data-testid^="source-badge-"]').first()).toBeVisible({ timeout: 10000 });
    });

    test('Ctrl+K focuses search input from anywhere on page', async ({ page }) => {
        await page.goto('/search');
        // Click elsewhere
        await page.locator('body').click();
        await page.keyboard.press('Control+k');
        const input = page.locator('[role="textbox"]').first();
        await expect(input).toBeFocused();
    });
});
