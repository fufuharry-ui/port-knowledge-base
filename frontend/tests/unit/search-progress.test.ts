/**
 * tests/unit/search-progress.test.ts — 搜索进度反馈 TDD 测试 (落地增强)
 * 验证 sse.ts 解析 thought 事件并触发 onThought 回调
 */

// jsdom 无 TextEncoder,补 polyfill
const { TextEncoder, TextDecoder } = require('util');
global.TextEncoder = TextEncoder;
global.TextDecoder = TextDecoder;

// Mock global fetch + ReadableStream
const mockFetch = jest.fn();
global.fetch = mockFetch;

let apiModule: typeof import('@/lib/sse');

beforeAll(async () => {
    apiModule = await import('@/lib/sse');
});

beforeEach(() => {
    mockFetch.mockReset();
});

/** 构造一个伪 SSE 响应流,按 chunks 发送给定行 */
function mockSSEResponse(lines: string[]) {
    const encoder = new TextEncoder();
    const chunks = lines.map(l => encoder.encode(`data: ${l}\n\n`));
    return {
        ok: true,
        body: {
            getReader: () => {
                let i = 0;
                return {
                    read: async () => {
                        if (i < chunks.length) {
                            return { done: false, value: chunks[i++] };
                        }
                        return { done: true, value: undefined };
                    },
                };
            },
        },
    };
}

describe('sse thought event parsing', () => {
    test('onThought 回调被 thought 事件触发(进度反馈)', async () => {
        mockFetch.mockResolvedValueOnce(mockSSEResponse([
            JSON.stringify({ type: 'thought', step: 1, message: '🔍 初筛候选文档...' }),
            JSON.stringify({ type: 'thought', step: 2, message: '🧠 LLM 精选 Top-5...' }),
            JSON.stringify({ delta: '答案内容' }),
            '[DONE]',
        ]));

        const thoughts: Array<{ step: number; message: string }> = [];
        const deltas: string[] = [];

        await new Promise<void>((resolve) => {
            apiModule.startStreamSearch('q', {
                onThought: (step, message) => thoughts.push({ step, message }),
                onDelta: (t) => deltas.push(t),
                onDone: () => resolve(),
                onError: () => resolve(),
            });
        });

        expect(thoughts).toHaveLength(2);
        expect(thoughts[0].step).toBe(1);
        expect(thoughts[0].message).toContain('初筛');
        expect(thoughts[1].step).toBe(2);
        expect(deltas).toEqual(['答案内容']);
    });

    test('无 thought 事件时不调 onThought(向后兼容)', async () => {
        mockFetch.mockResolvedValueOnce(mockSSEResponse([
            JSON.stringify({ delta: '答案' }),
            '[DONE]',
        ]));

        let thoughtCalled = false;
        await new Promise<void>((resolve) => {
            apiModule.startStreamSearch('q', {
                onThought: () => { thoughtCalled = true; },
                onDelta: () => {},
                onDone: () => resolve(),
                onError: () => resolve(),
            });
        });

        expect(thoughtCalled).toBe(false);
    });
});
