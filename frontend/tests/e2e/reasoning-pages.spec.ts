/**
 * tests/e2e/reasoning-pages.spec.ts — 推理能力页面可见性 e2e 守卫 (Big-Loop #4)
 *
 * 用 Playwright route 拦截 mock 后端响应,无需真实 API Key。
 * 守卫三个新页面(/ontology、/entity-graph、/consistency)的核心可见内容——
 * 防止未来 api.ts 形状变化悄悄断页(评审发现 #1 的回归网)。
 */
import { test, expect } from '@playwright/test';

const API = 'http://localhost:8000';

test.describe('Big-Loop #4 推理页面可见性', () => {

    test('F-1: /ontology 渲染本体树 + 节点数', async ({ page }) => {
        await page.route(`${API}/api/v1/ontology`, route =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    ontology_tree: [
                        { term: '智慧港口', parent: null, definition: '根概念', children: [
                            { term: '港口自动化', parent: '智慧港口', definition: '自动化', children: [] },
                        ]},
                    ],
                    total_nodes: 42,
                    last_updated: '2026-06-29T21:49:00+08:00',
                }),
            })
        );

        await page.goto('/ontology');
        await expect(page.getByRole('heading', { name: '本体知识树' })).toBeVisible();
        await expect(page.getByText('智慧港口', { exact: true })).toBeVisible();
        await expect(page.getByText('根概念')).toBeVisible();
        await expect(page.getByText('42', { exact: true })).toBeVisible();
    });

    test('F-2: /entity-graph 查询后显示邻居 + 关系边', async ({ page }) => {
        await page.route(`${API}/api/v1/entity-graph*`, route =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    term: '5G技术', depth: 2,
                    neighbors: ['eMBB', 'uRLLC'],
                    edges: [
                        { source: 'eMBB', target: '5G技术', type: 'part_of', confidence: 0.9, doc_id: 'doc_001' },
                    ],
                    total_edges: 10,
                }),
            })
        );

        await page.goto('/entity-graph');
        await page.getByRole('button', { name: '探索' }).click();
        await expect(page.getByText('eMBB').first()).toBeVisible();
        await expect(page.getByText('相关关系边')).toBeVisible();
        await expect(page.getByRole('cell', { name: 'eMBB' })).toBeVisible();
    });

    test('F-3: /consistency 矛盾为 0 时诚实展示一致', async ({ page }) => {
        await page.route(`${API}/api/v1/consistency*`, route =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    status: 'success', total: 0, candidates_checked: 15,
                    last_updated: '2026-06-29T22:43:00+08:00',
                    contradictions: [],
                }),
            })
        );

        await page.goto('/consistency');
        await expect(page.getByRole('heading', { name: '一致性稽核' })).toBeVisible();
        await expect(page.getByText('知识库内一致,未检出矛盾')).toBeVisible();
        await expect(page.getByText('15', { exact: true })).toBeVisible();
        await expect(page.getByRole('button', { name: /触发稽核/ })).toBeVisible();
    });

    test('F-3b: /consistency 有矛盾时展示冲突列表', async ({ page }) => {
        await page.route(`${API}/api/v1/consistency*`, route =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    status: 'success', total: 1, candidates_checked: 15,
                    last_updated: '2026-06-29T22:43:00+08:00',
                    contradictions: [{
                        doc_a: 'doc_A', doc_b: 'doc_B',
                        conflict_point: '端到端延迟要求',
                        reasoning_chain: 'A说≤10ms,B说≤20ms',
                        confidence: 0.85,
                    }],
                }),
            })
        );

        await page.goto('/consistency');
        await expect(page.getByText('检出 1 处跨文档矛盾')).toBeVisible();
        await expect(page.getByText('端到端延迟要求')).toBeVisible();
        await expect(page.getByText(/10ms.*20ms|A说.*B说/)).toBeVisible();
    });

    test('F-6: NavBar 含三个新入口', async ({ page }) => {
        await page.route(`${API}/api/v1/ontology`, route =>
            route.fulfill({
                status: 200, contentType: 'application/json',
                body: JSON.stringify({ ontology_tree: [], total_nodes: 0 }),
            })
        );
        await page.goto('/ontology');
        // 等 NavBar 渲染就绪
        await expect(page.locator('header nav')).toBeVisible();
        await expect(page.locator('header nav a[href="/ontology"]')).toBeVisible();
        await expect(page.locator('header nav a[href="/entity-graph"]')).toBeVisible();
        await expect(page.locator('header nav a[href="/consistency"]')).toBeVisible();
    });
});
