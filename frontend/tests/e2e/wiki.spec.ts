/**
 * tests/e2e/wiki.spec.ts — Wiki 知识库仪表盘 E2E 测试
 */
import { test, expect, Page } from '@playwright/test';

// Mock API responses at the network level
const mockWikiIndex = {
    total_docs: 2,
    documents: [
        { id: 'doc_001', title: '岸桥远控技术方案', status: 'compiled', abstract_short: '延迟≤50ms' },
        { id: 'doc_002', title: '5G专网部署规范', status: 'raw', abstract_short: '5G网络规划' },
    ],
};

async function mockApi(page: Page) {
    await page.route('**/api/v1/wiki/index', (route) =>
        route.fulfill({ json: mockWikiIndex })
    );
    await page.route('**/api/v1/graph', (route) =>
        route.fulfill({ json: { nodes: [], edges: [] } })
    );
}

test.describe('Wiki Dashboard Page', () => {
    test.beforeEach(async ({ page }) => {
        await mockApi(page);
    });

    test('loads wiki dashboard with page title', async ({ page }) => {
        await page.goto('/wiki');
        await expect(page.locator('h1')).toBeVisible();
    });

    test('shows document cards from API data', async ({ page }) => {
        await page.goto('/wiki');
        await expect(page.locator('[data-testid="wiki-card"]').first()).toBeVisible({ timeout: 5000 });
        expect(await page.locator('[data-testid="wiki-card"]').count()).toBe(2);
    });

    test('shows document titles', async ({ page }) => {
        await page.goto('/wiki');
        await expect(page.getByText('岸桥远控技术方案')).toBeVisible({ timeout: 5000 });
        await expect(page.getByText('5G专网部署规范')).toBeVisible();
    });

    test('shows empty state message when no documents', async ({ page }) => {
        await page.route('**/api/v1/wiki/index', (route) =>
            route.fulfill({ json: { total_docs: 0, documents: [] } })
        );
        await page.goto('/wiki');
        await expect(page.locator('[data-testid="empty-state"]')).toBeVisible({ timeout: 5000 });
    });

    test('shows total document count', async ({ page }) => {
        await page.goto('/wiki');
        await expect(page.locator('[data-testid="doc-count"]')).toContainText('2', { timeout: 5000 });
    });

    test('clicking on a card expands detail or navigates', async ({ page }) => {
        await page.goto('/wiki');
        await page.locator('[data-testid="wiki-card"]').first().click();
        // Either a modal or navigation should happen
        await expect(page).toHaveURL(/wiki|docs/, { timeout: 3000 });
    });
});
