# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: qa.spec.ts >> PortGPT Q&A Streaming UAT >> UAT-04: Knowledge Graph dynamic highlight sync
- Location: tests\e2e\qa.spec.ts:86:9

# Error details

```
TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('[data-testid="chat-panel"]')

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - banner [ref=e2]:
    - navigation [ref=e3]:
      - link "KnowledgeBase v2.0" [ref=e4] [cursor=pointer]:
        - /url: /wiki
        - generic [ref=e5]: KnowledgeBase
        - generic [ref=e6]: v2.0
      - generic [ref=e7]:
        - link "📚 知识库" [ref=e8] [cursor=pointer]:
          - /url: /wiki
          - generic [ref=e9]: 📚
          - text: 知识库
        - link "🔍 检索" [ref=e10] [cursor=pointer]:
          - /url: /search
          - generic [ref=e11]: 🔍
          - text: 检索
        - link "🕸️ 知识图谱" [ref=e12] [cursor=pointer]:
          - /url: /graph
          - generic [ref=e13]: 🕸️
          - text: 知识图谱
        - link "⬆️ 上传" [ref=e14] [cursor=pointer]:
          - /url: /upload
          - generic [ref=e15]: ⬆️
          - text: 上传
  - main [ref=e16]:
    - generic [ref=e17]:
      - generic [ref=e18]:
        - generic [ref=e19]: 🕸️
        - generic [ref=e20]:
          - heading "知识图谱 · 智能问答" [level=1] [ref=e21]
          - paragraph [ref=e22]: 文档关系网络 · Q&A 推理轨迹 · 引用溯源 · 图谱联动高亮
      - generic [ref=e23]: ⚠ 加载失败：Failed to fetch
  - button "Open Next.js Dev Tools" [ref=e29] [cursor=pointer]:
    - img [ref=e30]
  - alert [ref=e33]
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test';
  2   | 
  3   | test.describe('PortGPT Q&A Streaming UAT', () => {
  4   |     test.setTimeout(120000);
  5   | 
  6   |     test.beforeEach(async ({ page }) => {
  7   |         // Go to graph page which contains the Q&A panel
  8   |         await page.goto('/graph');
  9   |         // Check what's going on by waiting a bit
  10  |         await page.waitForTimeout(5000);
  11  | 
  12  |         try {
> 13  |             await page.waitForSelector('[data-testid="chat-panel"]', { state: 'attached', timeout: 5000 });
      |                        ^ TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
  14  |         } catch (e) {
  15  |             console.error("DOM at timeout:\n", await page.content());
  16  |             throw e;
  17  |         }
  18  |     });
  19  | 
  20  |     test('UAT-01: Layout and basic elements are present', async ({ page }) => {
  21  |         await expect(page.getByText('知识图谱 · 智能问答')).toBeVisible();
  22  |         await expect(page.getByTestId('chat-panel')).toBeVisible();
  23  |         await expect(page.getByTestId('chat-input')).toBeVisible();
  24  |         await expect(page.getByTestId('chat-submit')).toBeVisible();
  25  |     });
  26  | 
  27  |     test('UAT-02: End-to-end question and streaming response', async ({ page }) => {
  28  |         // Setup text
  29  |         const query = '什么是边缘计算？';
  30  |         const input = page.getByTestId('chat-input');
  31  |         await input.fill(query);
  32  | 
  33  |         // Ensure submit button is enabled then click
  34  |         const submitBtn = page.getByTestId('chat-submit');
  35  |         await expect(submitBtn).not.toBeDisabled();
  36  |         await submitBtn.click();
  37  | 
  38  |         // 1. Check user question bubble
  39  |         const userBubble = page.locator('[data-testid="chat-bubble"][data-role="user"]').last();
  40  |         await expect(userBubble).toContainText(query);
  41  | 
  42  |         // 2. Wait for AI response (stream starts)
  43  |         const aiBubble = page.locator('[data-testid="chat-bubble"][data-role="assistant"]').last();
  44  |         await expect(aiBubble).toBeVisible();
  45  | 
  46  |         // 3. Wait for thought trace to appear before final completion
  47  |         const thoughtTrace = page.getByTestId('thought-trace');
  48  |         await expect(thoughtTrace).toBeVisible();
  49  | 
  50  |         // Check if steps appear (like Layer 1, Layer 2)
  51  |         // Wait till stream stops (button re-enables or '推理中' indicator hides)
  52  |         // The word "推理中" is present during stream
  53  |         await expect(page.getByText('推理中')).toBeHidden({ timeout: 20000 });
  54  | 
  55  |         // 4. Validate answer text
  56  |         await expect(aiBubble).not.toBeEmpty();
  57  |         const content = await aiBubble.textContent();
  58  |         expect(content?.length).toBeGreaterThan(10);
  59  |     });
  60  | 
  61  |     test('UAT-03: Citation parsing and tooltips', async ({ page }) => {
  62  |         const query = '5G专网的作用是什么？';
  63  |         await page.getByTestId('chat-input').fill(query);
  64  |         await page.getByTestId('chat-submit').click();
  65  | 
  66  |         // Wait for stream to finish
  67  |         // We look for the last assistant bubble
  68  |         const aiBubble = page.locator('[data-testid="chat-bubble"][data-role="assistant"]').last();
  69  |         // Wait till '推理中' goes away to ensure its done
  70  |         await expect(page.getByText('推理中')).toBeHidden({ timeout: 25000 });
  71  | 
  72  |         // Check if there are citation markers e.g., [1]
  73  |         const citationMark = aiBubble.getByTestId('citation-trigger').first();
  74  |         // Skip if LLM decided not to provide citations for this specific run
  75  |         const ctCount = await citationMark.count();
  76  |         if (ctCount > 0) {
  77  |             await expect(citationMark).toBeVisible();
  78  |             await citationMark.hover();
  79  |             // Radix UI tooltip pushes to portal, look at body
  80  |             const tooltipContent = page.getByRole('tooltip');
  81  |             await expect(tooltipContent).toBeVisible();
  82  |             await expect(tooltipContent).toContainText('📄');
  83  |         }
  84  |     });
  85  | 
  86  |     test('UAT-04: Knowledge Graph dynamic highlight sync', async ({ page }) => {
  87  |         // Look for knowledge graph container
  88  |         await expect(page.getByTestId('knowledge-graph')).toBeVisible();
  89  | 
  90  |         // Ask question to trigger entity matching
  91  |         await page.getByTestId('chat-input').fill('岸桥架构由什么组成？');
  92  |         await page.getByTestId('chat-submit').click();
  93  | 
  94  |         // Wait for the highlight set to appear on the UI 
  95  |         // We render a UI text: "🔆 Q&A 命中节点" when highlightIds length > 0
  96  |         const highlightBanner = page.locator('text=/Q&A 命中节点/');
  97  |         await expect(highlightBanner).toBeVisible({ timeout: 15000 });
  98  |     });
  99  | });
  100 | 
```