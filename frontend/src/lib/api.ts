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

// ─── Big-Loop #4: 推理能力类型(本体/实体图谱/一致性) ──────────────────────

export interface OntologyNode {
    term: string;
    parent: string | null;
    definition?: string;
    children?: OntologyNode[];
}

export interface OntologyData {
    ontology_tree: OntologyNode[];
    total_nodes: number;
    last_updated?: string;
}

export interface EntityGraphData {
    term: string;
    depth: number;
    neighbors: string[];
    edges: Array<{
        source: string;
        target: string;
        type?: string;
        confidence?: number;
        evidence?: string;
        doc_id?: string;
    }>;
    total_edges: number;
}

export interface Contradiction {
    doc_a: string;
    doc_b: string;
    conflict_point?: string;
    reasoning_chain?: string;
    confidence?: number;
    detected_at?: string;
}

export interface ConsistencyReport {
    status: string;
    total: number;
    candidates_checked?: number;
    last_updated?: string;
    contradictions: Contradiction[];
    /** POST 失败时后端返回的错误说明(status === 'error') */
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

/** 删除文档 + 全部产物 + 清理引用(Loop #10) */
export async function deleteDoc(docId: string): Promise<{ status: string; removed: boolean }> {
    const res = await fetch(`${API_BASE}/api/v1/docs/${docId}`, { method: 'DELETE' });
    return handleResponse(res);
}

/** 重置文档状态并触发重编译(error 文档重试,Loop #10) */
export async function recompileDoc(docId: string): Promise<{ status: string }> {
    const res = await fetch(`${API_BASE}/api/v1/docs/${docId}/recompile`, { method: 'POST' });
    return handleResponse(res);
}

// ─── Big-Loop #4: 推理能力 API(本体/实体图谱/一致性) ──────────────────────────

/** 获取全局本体树 (Loop #1) */
export async function fetchOntology(): Promise<OntologyData> {
    const res = await fetch(`${API_BASE}/api/v1/ontology`);
    return handleResponse<OntologyData>(res);
}

/** 获取术语的实体邻居图谱 (Loop #2)。depth 默认 1。 */
export async function fetchEntityGraph(term: string, depth: number = 1): Promise<EntityGraphData> {
    const url = `${API_BASE}/api/v1/entity-graph?term=${encodeURIComponent(term)}&depth=${depth}`;
    const res = await fetch(url);
    return handleResponse<EntityGraphData>(res);
}

/** 读取已知矛盾报告(只读 GET,Loop #3) */
export async function fetchConsistency(): Promise<ConsistencyReport> {
    const res = await fetch(`${API_BASE}/api/v1/consistency`);
    return handleResponse<ConsistencyReport>(res);
}

/** 触发全库一致性稽核(POST,Loop #3)。返回刷新后的报告。 */
export async function triggerConsistencyCheck(): Promise<ConsistencyReport> {
    const res = await fetch(`${API_BASE}/api/v1/consistency`, { method: 'POST' });
    return handleResponse<ConsistencyReport>(res);
}
