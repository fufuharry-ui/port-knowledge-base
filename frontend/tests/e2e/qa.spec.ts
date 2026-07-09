import { test, expect } from '@playwright/test';

test.describe('PortGPT Q&A Streaming UAT', () => {
    test.setTimeout(120000);

    test.beforeEach(async ({ page }) => {
        // Go to graph page which contains the Q&A panel
        await page.goto('/graph');
        // Check what's going on by waiting a bit
        await page.waitForTimeout(5000);

        try {
            await page.waitForSelector('[data-testid="chat-panel"]', { state: 'attached', timeout: 5000 });
        } catch (e) {
            console.error("DOM at timeout:\n", await page.content());
            throw e;
        }
    });

    test('UAT-01: Layout and basic elements are present', async ({ page }) => {
        await expect(page.getByText('知识图谱 · 智能问答')).toBeVisible();
        await expect(page.getByTestId('chat-panel')).toBeVisible();
        await expect(page.getByTestId('chat-input')).toBeVisible();
        await expect(page.getByTestId('chat-submit')).toBeVisible();
    });

    test('UAT-02: End-to-end question and streaming response', async ({ page }) => {
        // Setup text
        const query = '什么是边缘计算？';
        const input = page.getByTestId('chat-input');
        await input.fill(query);

        // Ensure submit button is enabled then click
        const submitBtn = page.getByTestId('chat-submit');
        await expect(submitBtn).not.toBeDisabled();
        await submitBtn.click();

        // 1. Check user question bubble
        const userBubble = page.locator('[data-testid="chat-bubble"][data-role="user"]').last();
        await expect(userBubble).toContainText(query);

        // 2. Wait for AI response (stream starts)
        const aiBubble = page.locator('[data-testid="chat-bubble"][data-role="assistant"]').last();
        await expect(aiBubble).toBeVisible();

        // 3. Wait for thought trace to appear before final completion
        const thoughtTrace = page.getByTestId('thought-trace');
        await expect(thoughtTrace).toBeVisible();

        // Check if steps appear (like Layer 1, Layer 2)
        // Wait till stream stops (button re-enables or '推理中' indicator hides)
        // The word "推理中" is present during stream
        await expect(page.getByText('推理中')).toBeHidden({ timeout: 20000 });

        // 4. Validate answer text
        await expect(aiBubble).not.toBeEmpty();
        const content = await aiBubble.textContent();
        expect(content?.length).toBeGreaterThan(10);
    });

    test('UAT-03: Citation parsing and tooltips', async ({ page }) => {
        const query = '5G专网的作用是什么？';
        await page.getByTestId('chat-input').fill(query);
        await page.getByTestId('chat-submit').click();

        // Wait for stream to finish
        // We look for the last assistant bubble
        const aiBubble = page.locator('[data-testid="chat-bubble"][data-role="assistant"]').last();
        // Wait till '推理中' goes away to ensure its done
        await expect(page.getByText('推理中')).toBeHidden({ timeout: 25000 });

        // Check if there are citation markers e.g., [1]
        const citationMark = aiBubble.getByTestId('citation-trigger').first();
        // Skip if LLM decided not to provide citations for this specific run
        const ctCount = await citationMark.count();
        if (ctCount > 0) {
            await expect(citationMark).toBeVisible();
            await citationMark.hover();
            // Radix UI tooltip pushes to portal, look at body
            const tooltipContent = page.getByRole('tooltip');
            await expect(tooltipContent).toBeVisible();
            await expect(tooltipContent).toContainText('📄');
        }
    });

    test('UAT-04: Knowledge Graph dynamic highlight sync', async ({ page }) => {
        // Look for knowledge graph container
        await expect(page.getByTestId('knowledge-graph')).toBeVisible();

        // Ask question to trigger entity matching
        await page.getByTestId('chat-input').fill('岸桥架构由什么组成？');
        await page.getByTestId('chat-submit').click();

        // Wait for the highlight set to appear on the UI 
        // We render a UI text: "🔆 Q&A 命中节点" when highlightIds length > 0
        const highlightBanner = page.locator('text=/Q&A 命中节点/');
        await expect(highlightBanner).toBeVisible({ timeout: 15000 });
    });
});
