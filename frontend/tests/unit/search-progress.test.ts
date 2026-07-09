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
    test('onThought callback triggered by thought events', async () => {
        mockFetch.mockResolvedValueOnce(mockSSEResponse([
            JSON.stringify({ type: 'thought', step: 1, message: 'Layer 1 BM25...' }),
            JSON.stringify({ type: 'thought', step: 2, message: 'Layer 2 LLM...' }),
            JSON.stringify({ delta: 'Answer content' }),
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
        expect(thoughts[1].step).toBe(2);
        expect(deltas).toEqual(['Answer content']);
    });

    test('no onThought call when no thought events', async () => {
        mockFetch.mockResolvedValueOnce(mockSSEResponse([
            JSON.stringify({ delta: 'Answer' }),
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
