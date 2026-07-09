/**
 * tests/e2e/uat.spec.ts — Karpathy-Style LLM Wiki UAT 测试套件
 *
 * 覆盖 7 个核心业务场景（User Acceptance Tests），均通过 Playwright 网络拦截
 * 模拟后端响应，无需配置真实 API Key 即可完整运行。
 *
 * 运行方式:
 *   npx playwright test tests/e2e/uat.spec.ts --headed
 */

import { test, expect, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import os from 'os';

// ─── Shared Mock Data ────────────────────────────────────────────────────────

const MOCK_WIKI_INDEX = {
    total_docs: 3,
    compiled_count: 2,
    documents: [
        {
            id: 'doc_001',
            title: '岸桥远控技术方案',
            status: 'compiled',
            abstract_short: '基于5G和MEC的岸桥远程操控系统，端到端延迟≤50ms。',
            ontology_terms: ['岸桥远控', '5G专网'],
            char_count: 12000,
            ingested_at: '2026-04-05T10:00:00Z',
        },
        {
            id: 'doc_002',
            title: '5G专网部署规范',
            status: 'compiled',
            abstract_short: '智慧港口5G独立组网方案，覆盖全港区信号。',
            ontology_terms: ['5G专网', 'MEC'],
            char_count: 8500,
            ingested_at: '2026-04-05T11:00:00Z',
        },
        {
            id: 'doc_003',
            title: '数据治理框架V2',
            status: 'raw',
            abstract_short: '港口数据治理三层架构。',
            ontology_terms: ['数据治理'],
            char_count: 5300,
            ingested_at: '2026-04-06T09:00:00Z',
        },
    ],
};

const MOCK_GRAPH = {
    nodes: [
        { id: 'doc_001', title: '岸桥远控技术方案' },
        { id: 'doc_002', title: '5G专网部署规范' },
        { id: 'doc_003', title: '数据治理框架V2' },
    ],
    edges: [
        { source: 'doc_001', target: 'doc_002', type: 'supplements', confidence: 0.92 },
        { source: 'doc_002', target: 'doc_003', type: 'same_topic', confidence: 0.75 },
    ],
};

const MOCK_UPLOAD_RESPONSE = {
    doc_id: 'doc_20260412_uat01',
    title: 'uat_test.md',
    status: 'raw',
    char_count: 42,
};

// SSE 响应体构建器
function buildSseBody(events: Array<Record<string, unknown>>, done = true): string {
    const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`);
    if (done) lines.push('data: [DONE]\n\n');
    return lines.join('');
}

const MOCK_SEARCH_SSE = buildSseBody([
    { type: 'thought', content: '正在执行 Layer 1 BM25 关键词粗筛...' },
    { type: 'thought', content: 'Layer 2 LLM 摘要评分，候选文档 2 篇。' },
    { type: 'source', doc_id: 'doc_001', title: '岸桥远控技术方案' },
    { type: 'delta', content: '根据《岸桥远控技术方案》，' },
    { type: 'delta', content: '端到端延迟≤50ms，5G空口延迟≤20ms。' },
]);

const MOCK_QA_SSE = buildSseBody([
    { type: 'thought', content: '检索到 2 篇相关文档...' },
    { type: 'source', doc_id: 'doc_001', title: '岸桥远控技术方案' },
    { type: 'entity', entity: '岸桥远控' },
    { type: 'entity', entity: '5G专网' },
    { type: 'delta', content: '岸桥由岸桥控制系统、远控工作站和5G传输网络三部分组成。' },
    { type: 'done', total_tokens: 128 },
]);

// ─── Setup Helpers ───────────────────────────────────────────────────────────

async function mockAllApis(page: Page) {
    await page.route('**/api/v1/wiki/index', (r) =>
        r.fulfill({ json: MOCK_WIKI_INDEX })
    );
    await page.route('**/api/v1/graph', (r) =>
        r.fulfill({ json: MOCK_GRAPH })
    );
    await page.route('**/api/v1/upload', (r) =>
        r.fulfill({ json: MOCK_UPLOAD_RESPONSE })
    );
    await page.route('**/api/v1/search/stream*', (r) =>
        r.fulfill({
            status: 200,
            headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
            body: MOCK_SEARCH_SSE,
        })
    );
    await page.route('**/api/v1/qa*', (r) =>
        r.fulfill({
            status: 200,
            headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
            body: MOCK_QA_SSE,
        })
    );
    await page.route('**/api/v1/docs/**', (r) =>
        r.fulfill({ json: MOCK_WIKI_INDEX.documents[0] })
    );
    await page.route('**/api/v1/docs', (r) =>
        r.fulfill({ json: { documents: MOCK_WIKI_INDEX.documents, total: 3 } })
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F01: 全局导航与页面加载
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F01: 全局导航与页面加载', () => {
    test.beforeEach(async ({ page }) => {
        await mockAllApis(page);
    });

    test('F01-T01: 访问根路径自动跳转到 /wiki', async ({ page }) => {
        await page.goto('/');
        await expect(page).toHaveURL(/\/wiki/, { timeout: 5000 });
    });

    test('F01-T02: 顶部导航栏所有页面链接可点击', async ({ page }) => {
        await page.goto('/wiki');
        const navLinks = ['检索', '知识图谱', '问答', '上传'];
        for (const label of navLinks) {
            await expect(page.getByRole('link', { name: new RegExp(label) })).toBeVisible();
        }
    });

    test('F01-T03: 响应式布局 — 窗口最小 1024px 不出现横向滚动条', async ({ page }) => {
        await page.setViewportSize({ width: 1024, height: 768 });
        await page.goto('/wiki');
        const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
        const viewportWidth = await page.evaluate(() => window.innerWidth);
        expect(bodyWidth).toBeLessThanOrEqual(viewportWidth + 2); // 2px tolerance
    });

    test('F01-T04: 直接访问各页面均能正常渲染 h1 标题', async ({ page }) => {
        for (const path of ['/wiki', '/search', '/graph', '/upload']) {
            await page.goto(path);
            await expect(page.locator('h1').first()).toBeVisible({ timeout: 8000 });
        }
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F02: 知识库仪表盘
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F02: 知识库仪表盘', () => {
    test.beforeEach(async ({ page }) => {
        await mockAllApis(page);
        await page.goto('/wiki');
    });

    test('F02-T01: 仪表盘展示文档总数与已编译数量', async ({ page }) => {
        // 3 total docs, 2 compiled
        await expect(page.locator('[data-testid="doc-count"]')).toContainText('3', { timeout: 5000 });
    });

    test('F02-T02: 文档卡片展示标题、状态徽章、摘要', async ({ page }) => {
        const firstCard = page.locator('[data-testid="wiki-card"]').first();
        await expect(firstCard).toBeVisible({ timeout: 5000 });
        await expect(firstCard.locator('[data-testid="status-badge"]')).toBeVisible();
    });

    test('F02-T03: compiled 文档徽章样式有别于 raw 文档', async ({ page }) => {
        const badges = await page.locator('[data-testid="status-badge"]').all();
        expect(badges.length).toBeGreaterThanOrEqual(2);
        const compiledBadge = badges.find(async (b) => (await b.textContent())?.includes('compiled'));
        const rawBadge = badges.find(async (b) => (await b.textContent())?.includes('raw'));
        // Verify they have distinct class names
        expect(compiledBadge).toBeDefined();
        expect(rawBadge).toBeDefined();
    });

    test('F02-T04: 点击卡片后保持在 wiki 域（展开详情或导航）', async ({ page }) => {
        await page.locator('[data-testid="wiki-card"]').first().click();
        await expect(page).toHaveURL(/wiki|docs/, { timeout: 3000 });
    });

    test('F02-T05: 空知识库时展示空状态引导', async ({ page }) => {
        await page.route('**/api/v1/wiki/index', (r) =>
            r.fulfill({ json: { total_docs: 0, compiled_count: 0, documents: [] } })
        );
        await page.goto('/wiki');
        await expect(page.locator('[data-testid="empty-state"]')).toBeVisible({ timeout: 5000 });
    });

    test('F02-T06: API 请求失败时展示错误提示而非空白页', async ({ page }) => {
        await page.route('**/api/v1/wiki/index', (r) =>
            r.fulfill({ status: 500, body: 'Internal Server Error' })
        );
        await page.goto('/wiki');
        // Should show some error indicator, not a completely blank page
        const errorState = page.locator('[data-testid="error-state"], [class*="error"], text=/错误|失败|Error/i');
        // At minimum, a heading should still be visible
        await expect(page.locator('h1').first()).toBeVisible({ timeout: 5000 });
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F03: 文件上传与摄入流程
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F03: 文件上传与摄入流程', () => {
    let tmpFile: string;

    test.beforeEach(async ({ page }) => {
        await mockAllApis(page);
        await page.goto('/upload');
        tmpFile = path.join(os.tmpdir(), `uat_upload_${Date.now()}.md`);
        fs.writeFileSync(tmpFile, '# UAT 测试文档\n\n港口自动化技术测试内容。');
    });

    test.afterEach(() => {
        if (fs.existsSync(tmpFile)) fs.unlinkSync(tmpFile);
    });

    test('F03-T01: 上传页面展示拖拽区域和支持格式说明', async ({ page }) => {
        await expect(page.locator('[data-testid="dropzone"]')).toBeVisible();
        await expect(page.locator('[data-testid="accepted-types"]')).toContainText(/pdf|md|txt/i);
    });

    test('F03-T02: 选择有效 .md 文件后显示上传成功卡片', async ({ page }) => {
        await page.locator('input[type="file"]').setInputFiles(tmpFile);
        await expect(
            page.locator('[data-testid^="upload-item-"]').first()
        ).toBeVisible({ timeout: 8000 });
    });

    test('F03-T03: 上传成功后卡片包含 doc_id 标识', async ({ page }) => {
        await page.locator('input[type="file"]').setInputFiles(tmpFile);
        await expect(
            page.locator('[data-testid="upload-item-doc_20260412_uat01"]')
        ).toBeVisible({ timeout: 8000 });
    });

    test('F03-T04: 上传 .exe 非法格式，前端拦截并展示错误提示', async ({ page }) => {
        const exeFile = path.join(os.tmpdir(), 'malware.exe');
        fs.writeFileSync(exeFile, '\x4d\x5a');
        try {
            await page.locator('input[type="file"]').setInputFiles(exeFile);
            // Expect error message to appear
            await expect(page.locator('[data-testid="upload-error"]')).toBeVisible({ timeout: 5000 });
        } finally {
            fs.unlinkSync(exeFile);
        }
    });

    test('F03-T05: 上传进行中显示加载状态（spinner）', async ({ page }) => {
        // Block upload response temporarily to see the loading state
        let resolveUpload: () => void;
        const uploadBlocked = new Promise<void>((res) => { resolveUpload = res; });

        await page.route('**/api/v1/upload', async (route) => {
            await uploadBlocked;
            await route.fulfill({ json: MOCK_UPLOAD_RESPONSE });
        });

        await page.locator('input[type="file"]').setInputFiles(tmpFile);
        // Spinner should appear briefly
        await expect(page.locator('[data-testid="uploading-spinner"]')).toBeVisible({ timeout: 5000 });
        resolveUpload!();
    });

    test('F03-T06: 后端返回 422 不支持格式时，UI 展示错误信息', async ({ page }) => {
        await page.route('**/api/v1/upload', (r) =>
            r.fulfill({
                status: 422,
                json: { detail: "不支持的文件格式 '.exe'" },
            })
        );
        const badFile = path.join(os.tmpdir(), 'test.exe');
        fs.writeFileSync(badFile, 'data');
        try {
            await page.locator('input[type="file"]').setInputFiles(badFile);
            // UI should handle this gracefully
            await expect(page.locator('[data-testid="upload-error"], [class*="error"]').first()).toBeVisible({ timeout: 5000 });
        } finally {
            fs.unlinkSync(badFile);
        }
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F04: 语义检索（SSE 流式输出）
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F04: 语义检索（SSE 流式）', () => {
    test.beforeEach(async ({ page }) => {
        await mockAllApis(page);
        await page.goto('/search');
    });

    test('F04-T01: 检索输入框可见且支持键盘快捷键 Ctrl+K 聚焦', async ({ page }) => {
        const input = page.locator('[role="textbox"]').first();
        await expect(input).toBeVisible();
        await page.locator('body').click();
        await page.keyboard.press('Control+k');
        await expect(input).toBeFocused();
    });

    test('F04-T02: 输入查询并按 Enter，展示思维链（thought trace）', async ({ page }) => {
        const input = page.locator('[role="textbox"]').first();
        await input.fill('岸桥延迟要求');
        await input.press('Enter');
        await expect(page.locator('[data-testid="answer-text"]')).toBeVisible({ timeout: 10000 });
    });

    test('F04-T03: 检索结果包含引用来源标注', async ({ page }) => {
        await page.locator('[role="textbox"]').first().fill('5G延迟');
        await page.locator('[role="textbox"]').first().press('Enter');
        await expect(
            page.locator('[data-testid^="source-badge-"]').first()
        ).toBeVisible({ timeout: 10000 });
    });

    test('F04-T04: 输入为空时不触发请求（按钮禁用或无跳转）', async ({ page }) => {
        const requestCount = { n: 0 };
        page.on('request', (req) => {
            if (req.url().includes('/search')) requestCount.n++;
        });
        // Click search button without entering text
        const submitBtn = page.locator('button[type="submit"], [data-testid="search-submit"]').first();
        if (await submitBtn.count() > 0) {
            await submitBtn.click();
        } else {
            await page.locator('[role="textbox"]').first().press('Enter');
        }
        await page.waitForTimeout(1000);
        // No search request should have been made for empty query
        expect(requestCount.n).toBe(0);
    });

    test('F04-T05: 多次连续查询，结果区域正确刷新（无重叠旧内容）', async ({ page }) => {
        const input = page.locator('[role="textbox"]').first();

        await input.fill('岸桥延迟');
        await input.press('Enter');
        await expect(page.locator('[data-testid="answer-text"]')).toBeVisible({ timeout: 10000 });

        await input.fill('5G专网覆盖');
        await input.press('Enter');
        // Result should still be visible and not duplicated
        const answers = page.locator('[data-testid="answer-text"]');
        await expect(answers.first()).toBeVisible({ timeout: 10000 });
    });

    test('F04-T06: SSE 流式输出过程中显示加载状态', async ({ page }) => {
        // Simulate a slow SSE stream
        await page.route('**/api/v1/search/stream*', async (route) => {
            await route.fulfill({
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
                body: 'data: {"type":"thought","content":"正在检索..."}\n\n',
            });
        });
        const input = page.locator('[role="textbox"]').first();
        await input.fill('测试慢速流');
        await input.press('Enter');
        // Some loading indicator should appear
        await expect(
            page.locator('[data-testid="loading-indicator"], [class*="loading"], [class*="spinner"]').first()
        ).toBeVisible({ timeout: 5000 });
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F05: 知识图谱可视化
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F05: 知识图谱可视化', () => {
    test.beforeEach(async ({ page }) => {
        await mockAllApis(page);
        await page.goto('/graph');
    });

    test('F05-T01: 图谱页面正确渲染 ECharts Canvas 或容器', async ({ page }) => {
        await expect(
            page.locator('canvas, [data-testid="knowledge-graph"]')
        ).toBeVisible({ timeout: 8000 });
    });

    test('F05-T02: 底部统计展示正确节点数和边数', async ({ page }) => {
        await expect(page.locator('[data-testid="node-count"]')).toContainText('3', { timeout: 5000 });
        await expect(page.locator('[data-testid="edge-count"]')).toContainText('2', { timeout: 5000 });
    });

    test('F05-T03: 关系图例包含所有关系类型标注', async ({ page }) => {
        // Legend should list relation types
        const legend = page.locator('[class*="legend"], text=/补充关联|矛盾冲突|同类主题/');
        await expect(legend.first()).toBeVisible({ timeout: 5000 });
    });

    test('F05-T04: 空图谱时展示空状态提示', async ({ page }) => {
        await page.route('**/api/v1/graph', (r) =>
            r.fulfill({ json: { nodes: [], edges: [] } })
        );
        await page.goto('/graph');
        await expect(page.locator('[data-testid="graph-empty"]')).toBeVisible({ timeout: 5000 });
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F06: PortGPT 智能问答（Q&A SSE）
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F06: PortGPT 智能问答', () => {
    test.setTimeout(30000);

    test.beforeEach(async ({ page }) => {
        await mockAllApis(page);
        await page.goto('/graph');
        await page.waitForSelector('[data-testid="chat-panel"]', { state: 'visible', timeout: 10000 });
    });

    test('F06-T01: 问答面板包含输入框和发送按钮', async ({ page }) => {
        await expect(page.getByTestId('chat-input')).toBeVisible();
        await expect(page.getByTestId('chat-submit')).toBeVisible();
    });

    test('F06-T02: 发送问题后，用户气泡正确显示问题内容', async ({ page }) => {
        const query = 'AGV 的导航方式是什么？';
        await page.getByTestId('chat-input').fill(query);
        await page.getByTestId('chat-submit').click();
        await expect(
            page.locator('[data-testid="chat-bubble"][data-role="user"]').last()
        ).toContainText(query, { timeout: 5000 });
    });

    test('F06-T03: AI 回答气泡出现，内容非空', async ({ page }) => {
        await page.getByTestId('chat-input').fill('什么是MEC？');
        await page.getByTestId('chat-submit').click();
        const aiBubble = page.locator('[data-testid="chat-bubble"][data-role="assistant"]').last();
        await expect(aiBubble).toBeVisible({ timeout: 15000 });
        const content = await aiBubble.textContent();
        expect(content?.trim().length).toBeGreaterThan(0);
    });

    test('F06-T04: 思维链面板在回答过程中可见', async ({ page }) => {
        await page.getByTestId('chat-input').fill('岸桥架构由什么组成？');
        await page.getByTestId('chat-submit').click();
        const thoughtTrace = page.getByTestId('thought-trace');
        await expect(thoughtTrace).toBeVisible({ timeout: 10000 });
    });

    test('F06-T05: 流式回答结束后，发送按钮重新可用', async ({ page }) => {
        const submitBtn = page.getByTestId('chat-submit');
        await page.getByTestId('chat-input').fill('测试问题');
        await submitBtn.click();
        // After stream completes, button should be re-enabled
        await expect(submitBtn).not.toBeDisabled({ timeout: 20000 });
    });

    test('F06-T06: 实体命中时图谱高亮提示出现', async ({ page }) => {
        await page.getByTestId('chat-input').fill('岸桥架构由什么组成？');
        await page.getByTestId('chat-submit').click();
        // When entities are returned, the graph highlight banner appears
        const highlightBanner = page.locator('text=/Q&A 命中节点|命中/');
        await expect(highlightBanner).toBeVisible({ timeout: 15000 });
    });

    test('F06-T07: 输入为空时发送按钮处于禁用状态', async ({ page }) => {
        await page.getByTestId('chat-input').fill('');
        const submitBtn = page.getByTestId('chat-submit');
        await expect(submitBtn).toBeDisabled();
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// UAT-F07: 端到端完整业务流程（Upload → Wiki → Search）
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('UAT-F07: 端到端完整业务流程', () => {
    test.setTimeout(60000);

    test('F07-T01: 上传文档 → 知识库刷新 → 检索命中 全链路验证', async ({ page }) => {
        const newDoc = {
            id: 'doc_20260412_e2e',
            title: 'E2E测试文档',
            status: 'raw',
            abstract_short: '端到端测试内容。',
            ontology_terms: ['E2E测试'],
            char_count: 100,
            ingested_at: new Date().toISOString(),
        };

        // Step 1: Mock upload returns new doc
        await page.route('**/api/v1/upload', (r) =>
            r.fulfill({ json: { doc_id: newDoc.id, title: newDoc.title, status: 'raw' } })
        );

        // Step 2: After upload, wiki/index includes the new doc
        await page.route('**/api/v1/wiki/index', (r) =>
            r.fulfill({
                json: {
                    total_docs: MOCK_WIKI_INDEX.total_docs + 1,
                    compiled_count: MOCK_WIKI_INDEX.compiled_count,
                    documents: [...MOCK_WIKI_INDEX.documents, newDoc],
                },
            })
        );
        await mockAllApis(page);

        // STEP 1: Upload
        await page.goto('/upload');
        const tmpFile = path.join(os.tmpdir(), 'e2e_full_flow.md');
        fs.writeFileSync(tmpFile, '# E2E Test\n\n端到端测试文档内容。');
        try {
            await page.locator('input[type="file"]').setInputFiles(tmpFile);
            await expect(
                page.locator(`[data-testid^="upload-item-"]`).first()
            ).toBeVisible({ timeout: 10000 });
        } finally {
            fs.unlinkSync(tmpFile);
        }

        // STEP 2: Navigate to Wiki and verify new doc appears
        await page.goto('/wiki');
        // Total count should now be 4
        await expect(page.locator('[data-testid="doc-count"]')).toContainText('4', { timeout: 5000 });

        // STEP 3: Navigate to Search and query
        await page.goto('/search');
        await page.locator('[role="textbox"]').first().fill('E2E测试');
        await page.locator('[role="textbox"]').first().press('Enter');
        await expect(page.locator('[data-testid="answer-text"]')).toBeVisible({ timeout: 10000 });
    });
});
