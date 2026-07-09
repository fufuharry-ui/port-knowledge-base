/**
 * tests/unit/api.reasoning.test.ts — 推理能力 API 客户端 TDD 测试 (Big-Loop #4)
 * 验证 lib/api.ts 新增的本体/实体图谱/一致性三个端点封装
 */
const mockFetch = jest.fn();
global.fetch = mockFetch;

let apiModule: typeof import('@/lib/api');

beforeAll(async () => {
    apiModule = await import('@/lib/api');
});

beforeEach(() => {
    mockFetch.mockReset();
});

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

const ok = (data: unknown) => ({ ok: true, json: async () => data });

// ─── fetchOntology (Loop #1) ─────────────────────────────────────────────────

describe('fetchOntology', () => {
    test('returns ontology tree + total_nodes', async () => {
        const mockData = {
            ontology_tree: [
                { term: '智慧港口', parent: null, definition: '根', children: [] },
            ],
            total_nodes: 92,
            last_updated: '2026-06-29T00:00:00+08:00',
        };
        mockFetch.mockResolvedValueOnce(ok(mockData));

        const result = await apiModule.fetchOntology();
        expect(result.total_nodes).toBe(92);
        expect(result.ontology_tree).toHaveLength(1);
        expect(result.ontology_tree[0].term).toBe('智慧港口');
        expect(mockFetch).toHaveBeenCalledWith(`${API_BASE}/api/v1/ontology`);
    });

    test('throws on non-ok response', async () => {
        mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
        await expect(apiModule.fetchOntology()).rejects.toThrow();
    });
});

// ─── fetchEntityGraph (Loop #2) ──────────────────────────────────────────────

describe('fetchEntityGraph', () => {
    test('calls /entity-graph with term + depth query params', async () => {
        const mockData = {
            term: '5G技术',
            depth: 2,
            neighbors: ['eMBB', 'uRLLC'],
            edges: [{ source: 'eMBB', target: '5G技术', type: 'part_of' }],
            total_edges: 30,
        };
        mockFetch.mockResolvedValueOnce(ok(mockData));

        const result = await apiModule.fetchEntityGraph('5G技术', 2);
        expect(result.neighbors).toEqual(['eMBB', 'uRLLC']);
        expect(result.total_edges).toBe(30);
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/api/v1/entity-graph?term=${encodeURIComponent('5G技术')}&depth=2`
        );
    });

    test('default depth = 1 when omitted', async () => {
        mockFetch.mockResolvedValueOnce(ok({
            term: 'X', depth: 1, neighbors: [], edges: [], total_edges: 0,
        }));
        await apiModule.fetchEntityGraph('X');
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/api/v1/entity-graph?term=X&depth=1`
        );
    });
});

// ─── fetchConsistency (Loop #3 GET) ──────────────────────────────────────────

describe('fetchConsistency', () => {
    test('returns contradictions report (read-only GET)', async () => {
        const mockData = {
            status: 'success',
            total: 0,
            candidates_checked: 20,
            last_updated: '2026-06-29T22:43:50+08:00',
            contradictions: [],
        };
        mockFetch.mockResolvedValueOnce(ok(mockData));

        const result = await apiModule.fetchConsistency();
        expect(result.total).toBe(0);
        expect(result.candidates_checked).toBe(20);
        expect(result.contradictions).toEqual([]);
        expect(mockFetch).toHaveBeenCalledWith(`${API_BASE}/api/v1/consistency`);
    });
});

// ─── triggerConsistencyCheck (Loop #3 POST) ──────────────────────────────────

describe('triggerConsistencyCheck', () => {
    test('POSTs to /consistency and returns refreshed report', async () => {
        const mockData = {
            status: 'success',
            total: 1,
            candidates_checked: 20,
            last_updated: '2026-06-29T23:00:00+08:00',
            contradictions: [
                { doc_a: 'doc_A', doc_b: 'doc_B', conflict_point: '延迟', confidence: 0.85 },
            ],
        };
        mockFetch.mockResolvedValueOnce(ok(mockData));

        const result = await apiModule.triggerConsistencyCheck();
        expect(result.total).toBe(1);
        expect(result.contradictions[0].conflict_point).toBe('延迟');
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/api/v1/consistency`,
            { method: 'POST' }
        );
    });
});
