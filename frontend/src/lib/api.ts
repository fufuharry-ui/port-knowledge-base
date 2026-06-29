/**
 * src/lib/api.ts — 知识库 API 客户端
 * 封装所有后端 REST API 调用，提供类型安全接口
 */

export const API_BASE =
    process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

// ─── 类型定义 ─────────────────────────────────────────────────────────────────

export interface DocMeta {
    id: string;
    title?: string;
    status?: 'raw' | 'compiling' | 'compiled' | 'error' | 'deleted';
    char_count?: number;
    language?: string;
    ingested_at?: string;
    source_type?: string;
    abstract_short?: string;
    ontology_terms?: string[];
}

export interface WikiIndexData {
    total_docs: number;
    documents: DocMeta[];
}

export interface GraphNode {
    id: string;
    title?: string;
}

export interface GraphEdge {
    source: string;
    target: string;
    type: string;
    confidence?: number;
}

export interface GraphData {
    nodes: GraphNode[];
    edges: GraphEdge[];
}

export interface SearchSource {
    doc_id: string;
    title?: string;
}

export interface SearchResult {
    answer: string;
    sources: SearchSource[];
}

export interface UploadResult {
    doc_id?: string;
    title?: string;
    status?: string;
    char_count?: number;
    skipped?: boolean;
    message?: string;
}

// ─── 内部工具 ─────────────────────────────────────────────────────────────────

async function handleResponse<T>(res: Response): Promise<T> {
    if (!res.ok) {
        throw new Error(`API error ${res.status}: ${res.statusText}`);
    }
    return res.json();
}

// ─── API 方法 ─────────────────────────────────────────────────────────────────

/** 获取 Wiki 仪表盘统计快照 */
export async function fetchWikiIndex(): Promise<WikiIndexData> {
    const res = await fetch(`${API_BASE}/api/v1/wiki/index`);
    return handleResponse<WikiIndexData>(res);
}

/** 获取全局知识图谱数据 */
export async function fetchGraph(): Promise<GraphData> {
    const res = await fetch(`${API_BASE}/api/v1/graph`);
    return handleResponse<GraphData>(res);
}

/** 同步检索（JSON 响应） */
export async function searchSync(query: string): Promise<SearchResult> {
    if (!query.trim()) {
        throw new Error('查询不能为空');
    }
    const res = await fetch(`${API_BASE}/api/v1/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, stream: false }),
    });
    return handleResponse<SearchResult>(res);
}

/** 构造 SSE 流式检索 URL */
export function buildStreamUrl(query: string): string {
    return `${API_BASE}/api/v1/search/stream?q=${encodeURIComponent(query)}`;
}

/** 上传文件 */
export async function uploadFile(file: File): Promise<UploadResult> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/api/v1/upload`, {
        method: 'POST',
        body: formData,
    });
    return handleResponse<UploadResult>(res);
}

/** 获取单文档详情 */
export async function fetchDocDetail(docId: string): Promise<DocMeta> {
    const res = await fetch(`${API_BASE}/api/v1/docs/${docId}`);
    return handleResponse<DocMeta>(res);
}

/** 获取所有文档列表 */
export async function fetchDocList(): Promise<{ documents: DocMeta[]; total: number }> {
    const res = await fetch(`${API_BASE}/api/v1/docs`);
    return handleResponse(res);
}

/** 手动触发关系重算 */
export async function triggerRelate(docId: string): Promise<{ message: string }> {
    const res = await fetch(`${API_BASE}/api/v1/relate/${docId}`, { method: 'POST' });
    return handleResponse(res);
}
