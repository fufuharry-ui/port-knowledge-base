/**
 * tests/e2e/graph.spec.ts — 知识图谱可视化页 E2E 测试
 */
import { test, expect, Page } from '@playwright/test';

const mockGraph = {
    nodes: [
        { id: 'doc_001', title: '岸桥远控技术方案' },
        { id: 'doc_002', title: '5G专网部署规范' },
    ],
    edges: [
        { source: 'doc_001', target: 'doc_002', type: 'supplements', confidence: 0.9 },
    ],
};

async function mockGraphApi(page: Page) {
    await page.route('**/api/v1/graph', (route) =>
        route.fulfill({ json: mockGraph })
    );
}

test.describe('Knowledge Graph Page', () => {
    test.beforeEach(async ({ page }) => {
        await mockGraphApi(page);
    });

    test('loads graph page with visible heading', async ({ page }) => {
        await page.goto('/graph');
        await expect(page.locator('h1')).toBeVisible();
    });

    test('renders ECharts canvas or container', async ({ page }) => {
        await page.goto('/graph');
        // ECharts renders a canvas element
        await expect(
            page.locator('canvas, [data-testid="knowledge-graph"]')
        ).toBeVisible({ timeout: 8000 });
    });

    test('shows node count stat', async ({ page }) => {
        await page.goto('/graph');
        await expect(page.locator('[data-testid="node-count"]')).toContainText('2', { timeout: 5000 });
    });

    test('shows edge count stat', async ({ page }) => {
        await page.goto('/graph');
        await expect(page.locator('[data-testid="edge-count"]')).toContainText('1', { timeout: 5000 });
    });

    test('shows empty state for empty graph', async ({ page }) => {
        await page.route('**/api/v1/graph', (route) =>
            route.fulfill({ json: { nodes: [], edges: [] } })
        );
        await page.goto('/graph');
        await expect(page.locator('[data-testid="graph-empty"]')).toBeVisible({ timeout: 5000 });
    });
});
