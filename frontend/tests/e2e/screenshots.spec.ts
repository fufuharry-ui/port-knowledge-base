/**
 * tests/e2e/screenshots.spec.ts — Big-Loop #2 设计证据:全页面截图(mocked)
 * 与 api/main.py 真实契约对齐的 mock,覆盖全部 10 条路由的关键视觉状态。
 * 产物:../UAT测试日志/20260725/screenshots/*.png
 */
import { test, expect, type Page } from '@playwright/test';
import path from 'path';

const SHOTS = path.join(__dirname, '..', '..', '..', 'UAT测试日志', '20260725', 'screenshots');

const MOCK_WIKI_INDEX = {
    total_docs: 3,
    compiled_count: 2,
    documents: [
        {
            id: 'doc_001', title: '岸桥远控技术方案', status: 'compiled',
            abstract_short: '基于 5G 与 MEC 的岸桥远程操控系统,端到端延迟≤50ms,涵盖网络切片、冗余链路与 failover 策略。',
            char_count: 12480, ingested_at: '2026-04-05T10:00:00Z',
            ontology_terms: ['岸桥远控', '5G专网', '端到端延迟'],
        },
        {
            id: 'doc_002', title: '5G专网部署规范', status: 'compiled',
            abstract_short: '港口 5G 专网组网规范:基站选址、上行增强、网络切片 SLA 与验收指标。',
            char_count: 8320, ingested_at: '2026-04-06T09:30:00Z',
            ontology_terms: ['5G专网', '网络切片'],
        },
        {
            id: 'doc_003', title: '数据治理框架V2', status: 'raw',
            abstract_short: '港口数据资产目录、血缘追踪与质量稽核框架第二版。',
            char_count: 5210, ingested_at: '2026-04-07T14:20:00Z',
            ontology_terms: ['数据治理'],
        },
    ],
};

const MOCK_GRAPH = {
    nodes: MOCK_WIKI_INDEX.documents.map(d => ({ id: d.id, title: d.title })),
    edges: [
        { source: 'doc_001', target: 'doc_002', type: 'supplements', confidence: 0.92 },
        { source: 'doc_002', target: 'doc_003', type: 'same_topic', confidence: 0.75 },
    ],
};

const MOCK_ONTOLOGY = {
    total_nodes: 5,
    last_updated: '2026-07-25T10:00:00',
    ontology_tree: [
        {
            term: '智慧港口', parent: null, definition: '以数字化技术驱动的现代化港口形态',
            children: [
                { term: '港口自动化', parent: '智慧港口', definition: '装卸与水平运输自动化', children: [] },
                {
                    term: '通信技术', parent: '智慧港口', definition: '港口专用网络',
                    children: [{ term: '5G专网', parent: '通信技术', children: [] }],
                },
            ],
        },
    ],
};

const MOCK_ENTITY_GRAPH = {
    term: '5G专网', depth: 2,
    neighbors: ['网络切片', 'MEC', '岸桥远控'],
    edges: [
        { source: '岸桥远控', target: '5G专网', type: 'depends_on', confidence: 0.91, doc_id: 'doc_001' },
        { source: '5G专网', target: '网络切片', type: 'part_of', confidence: 0.84, doc_id: 'doc_002' },
    ],
    total_edges: 12,
};

const MOCK_CONSISTENCY = {
    status: 'ok', total: 1, candidates_checked: 3,
    last_updated: '2026-07-25T10:05:00',
    contradictions: [
        {
            doc_a: 'doc_001', doc_b: 'doc_002',
            conflict_point: '5G 空口延迟指标(20ms vs 25ms)',
            reasoning_chain: 'doc_001 第3章给出 20ms 目标值,doc_002 验收表记录 25ms 实测均值。',
            confidence: 0.81,
        },
    ],
};

function sseBody(events: Array<Record<string, unknown>>): string {
    return events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('') + 'data: [DONE]\n\n';
}

const MOCK_SEARCH_SSE = sseBody([
    { type: 'thought', step: 1, message: '🔍 初筛候选文档(BM25+本体扩展)...' },
    { type: 'thought', step: 2, message: '🧠 LLM 精选 Top-5(共2篇候选)...' },
    { delta: '📎 **来源：** `doc_001` 岸桥远控技术方案\n\n' },
    { delta: '## 结论\n\n岸桥远控系统要求**端到端延迟≤50ms**,' },
    { delta: '其中 5G 空口段预算≤20ms,其余分配给 MEC 处理与有线回传。\n\n- 网络切片保障 SLA\n- 双链路冗余兜底' },
]);

const MOCK_QA_SSE = sseBody([
    { type: 'thought', step: 1, message: '🔍 BM25 关键词初筛中...' },
    { type: 'thought', step: 2, message: '🧠 LLM 精选候选文档 (2 → Top-5)...' },
    { type: 'source', citations: [{ ref: '[1]', doc_id: 'doc_001', title: '岸桥远控技术方案', section: '第3章 网络方案' }] },
    { type: 'entity', ids: ['doc_001'] },
    { type: 'delta', text: '岸桥远控系统的端到端延迟要求为 **≤50ms**[1]。' },
    { type: 'delta', text: '\n\n其中 5G 空口延迟预算 ≤20ms,由网络切片与 MEC 边缘计算共同保障[1]。' },
    { type: 'done' },
]);

async function mockAll(page: Page) {
    await page.route('**/api/v1/wiki/index', r => r.fulfill({ json: MOCK_WIKI_INDEX }));
    await page.route('**/api/v1/graph', r => r.fulfill({ json: MOCK_GRAPH }));
    await page.route('**/api/v1/ontology', r => r.fulfill({ json: MOCK_ONTOLOGY }));
    await page.route('**/api/v1/entity-graph*', r => r.fulfill({ json: MOCK_ENTITY_GRAPH }));
    await page.route('**/api/v1/consistency', r => r.fulfill({ json: MOCK_CONSISTENCY }));
    await page.route('**/api/v1/docs/**', r => r.fulfill({ json: MOCK_WIKI_INDEX.documents[0] }));
    await page.route('**/api/v1/search/stream*', r => r.fulfill({
        status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: MOCK_SEARCH_SSE,
    }));
    await page.route('**/api/v1/qa*', r => r.fulfill({
        status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: MOCK_QA_SSE,
    }));
}

async function shot(page: Page, name: string) {
    await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

test.describe('Big-Loop #2 设计证据:全页面截图', () => {
    test.beforeEach(async ({ page }) => {
        await mockAll(page);
        await page.setViewportSize({ width: 1440, height: 900 });
    });

    test('capture all pages', async ({ page }) => {
        // 着陆页
        await page.goto('/');
        await expect(page.locator('h1')).toBeVisible();
        await shot(page, '00-home');

        // 仪表盘(等挂载错落动画沉降)
        await page.goto('/wiki');
        await expect(page.locator('[data-testid="wiki-card"]').first()).toBeVisible();
        await page.waitForTimeout(700);
        await shot(page, '01-wiki');

        // 上传
        await page.goto('/upload');
        await expect(page.getByTestId('dropzone')).toBeVisible();
        await shot(page, '02-upload');

        // 检索(带回答)
        await page.goto('/search');
        await page.locator('[role="textbox"]').first().fill('岸桥远控延迟要求');
        await page.locator('[role="textbox"]').first().press('Enter');
        await expect(page.getByTestId('source-badge-doc_001')).toBeVisible({ timeout: 10000 });
        await shot(page, '03-search-answer');

        // 问答(思维链 + markdown 回答 + 引用 tooltip)
        await page.goto('/qa');
        await page.getByTestId('chat-input').fill('岸桥远控的延迟要求是什么?');
        await page.getByTestId('chat-submit').click();
        const aiBubble = page.locator('[data-testid="chat-bubble"][data-role="assistant"]').last();
        await expect(aiBubble.getByTestId('citation-trigger').first()).toBeVisible({ timeout: 10000 });
        // mock SSE 瞬时完成,等气泡 fade-in 沉降再拍
        await page.waitForTimeout(700);
        await shot(page, '04-qa-answer');
        await aiBubble.getByTestId('citation-trigger').first().hover();
        await expect(page.getByRole('tooltip')).toBeVisible();
        await shot(page, '05-qa-citation-tooltip');

        // 图谱
        await page.goto('/graph');
        await expect(page.locator('canvas')).toBeVisible({ timeout: 10000 });
        await page.waitForTimeout(800); // 力导向布局稍稳定
        await shot(page, '06-graph');

        // 本体
        await page.goto('/ontology');
        await expect(page.getByText('智慧港口').first()).toBeVisible();
        await shot(page, '07-ontology');

        // 实体图谱(带结果)
        await page.goto('/entity-graph');
        await page.getByRole('button', { name: '探索' }).click();
        await expect(page.getByText('网络切片').first()).toBeVisible({ timeout: 10000 });
        await shot(page, '08-entity-graph');

        // 稽核
        await page.goto('/consistency');
        await expect(page.getByText(/冲突点/)).toBeVisible();
        await shot(page, '09-consistency');

        // 文档枢纽
        await page.goto('/wiki/doc_001');
        await expect(page.getByRole('heading', { name: '岸桥远控技术方案' })).toBeVisible();
        await shot(page, '10-dochub');
    });
});
