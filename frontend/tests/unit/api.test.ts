/**
 * tests/unit/api.test.ts — API 客户端层 TDD 测试
 * 验证 lib/api.ts 的所有方法
 */

// Mock global fetch
const mockFetch = jest.fn();
global.fetch = mockFetch;

// Import AFTER mock is set up
let apiModule: typeof import('@/lib/api');

beforeAll(async () => {
    apiModule = await import('@/lib/api');
});

beforeEach(() => {
    mockFetch.mockReset();
});

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

// ─── fetchWikiIndex ─────────────────────────────────────────────────────────

describe('fetchWikiIndex', () => {
    test('returns documents array from API', async () => {
        const mockData = {
            total_docs: 2,
            documents: [
                { id: 'doc_001', title: '岸桥方案', status: 'compiled' },
                { id: 'doc_002', title: '5G专网', status: 'raw' },
            ],
        };
        mockFetch.mockResolvedValueOnce({
            ok: true,
            json: async () => mockData,
        });

        const result = await apiModule.fetchWikiIndex();
        expect(result.total_docs).toBe(2);
        expect(result.documents).toHaveLength(2);
        expect(mockFetch).toHaveBeenCalledWith(`${API_BASE}/api/v1/wiki/index`);
    });

    test('throws on non-ok response', async () => {
        mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
        await expect(apiModule.fetchWikiIndex()).rejects.toThrow();
    });
});

// ─── fetchGraph ──────────────────────────────────────────────────────────────

describe('fetchGraph', () => {
    test('returns nodes and edges from API', async () => {
        const mockData = {
            nodes: [{ id: 'doc_001', title: '岸桥方案' }],
            edges: [{ source: 'doc_001', target: 'doc_002', type: 'supplements', confidence: 0.9 }],
        };
        mockFetch.mockResolvedValueOnce({ ok: true, json: async () => mockData });

        const result = await apiModule.fetchGraph();
        expect(result.nodes).toHaveLength(1);
        expect(result.edges).toHaveLength(1);
        expect(result.edges[0].confidence).toBe(0.9);
    });

    test('returns empty graph when API returns empty', async () => {
        mockFetch.mockResolvedValueOnce({
            ok: true,
            json: async () => ({ nodes: [], edges: [] }),
        });
        const result = await apiModule.fetchGraph();
        expect(result.nodes).toHaveLength(0);
        expect(result.edges).toHaveLength(0);
    });
});

// ─── searchSync ──────────────────────────────────────────────────────────────

describe('searchSync', () => {
    test('sends query and returns answer + sources', async () => {
        const mockData = {
            answer: '岸桥远控端到端延迟≤50ms',
            sources: [{ doc_id: 'doc_001', title: '岸桥方案' }],
        };
        mockFetch.mockResolvedValueOnce({ ok: true, json: async () => mockData });

        const result = await apiModule.searchSync('岸桥延迟');
        expect(result.answer).toContain('延迟');
        expect(result.sources).toHaveLength(1);
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/api/v1/search`,
            expect.objectContaining({ method: 'POST' })
        );
    });

    test('throws error on empty query', async () => {
        await expect(apiModule.searchSync('')).rejects.toThrow('查询不能为空');
    });

    test('throws on API error response', async () => {
        mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
        await expect(apiModule.searchSync('测试')).rejects.toThrow();
    });
});

// ─── uploadFile ─────────────────────────────────────────────────────────────

describe('uploadFile', () => {
    test('uploads file and returns doc_id', async () => {
        const mockData = { doc_id: 'doc_20260405_001', status: 'raw', title: 'test.md' };
        mockFetch.mockResolvedValueOnce({ ok: true, json: async () => mockData });

        const file = new File(['# Test'], 'test.md', { type: 'text/markdown' });
        const result = await apiModule.uploadFile(file);
        expect(result.doc_id).toBe('doc_20260405_001');
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/api/v1/upload`,
            expect.objectContaining({ method: 'POST' })
        );
    });

    test('returns skipped=true for duplicate', async () => {
        mockFetch.mockResolvedValueOnce({
            ok: true,
            json: async () => ({ skipped: true, message: '文件已存在' }),
        });
        const file = new File(['# Dup'], 'dup.md', { type: 'text/markdown' });
        const result = await apiModule.uploadFile(file);
        expect(result.skipped).toBe(true);
    });
});

// ─── fetchDocDetail ─────────────────────────────────────────────────────────

describe('fetchDocDetail', () => {
    test('returns doc metadata', async () => {
        mockFetch.mockResolvedValueOnce({
            ok: true,
            json: async () => ({ id: 'doc_001', title: '岸桥方案', status: 'compiled' }),
        });
        const result = await apiModule.fetchDocDetail('doc_001');
        expect(result.id).toBe('doc_001');
        expect(result.status).toBe('compiled');
    });

    test('throws 404 when doc not found', async () => {
        mockFetch.mockResolvedValueOnce({ ok: false, status: 404 });
        await expect(apiModule.fetchDocDetail('not_exist')).rejects.toThrow();
    });
});
