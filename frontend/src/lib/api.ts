/**
 * src/lib/api.ts — 知识库 API 客户端
 * 封装所有后端 REST API 调用，提供类型安全接口
 */

export const API_BASE =
    process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

// ─── 类型定义 ─────────────────────────────────────────────────────────────────

/** 文档终态编译/回滚失败的稳定机器错误码(见 raw meta error_code) */
export type DocumentCompileErrorCode =
    | 'llm_configuration'
    | 'service_unavailable'
    | 'timeout'
    | 'document_processing'
    | 'compile_failed'
    | 'interrupted'
    | 'rollback_failed';

/** 请求级拒绝的稳定机器错误码(见 409/503 detail.code) */
export type CompileRequestErrorCode =
    | 'compile_in_progress'
    | 'knowledge_base_busy'
    | 'compile_transaction_unavailable'
    | 'recovery_required';

/** 后端编译/回滚/请求拒绝的稳定机器错误码(见 detail.code) */
export type CompileErrorCode = DocumentCompileErrorCode | CompileRequestErrorCode;

/** 错误码 → 固定中文用户文案(不暴露后端原始 detail) */
const COMPILE_ERROR_MESSAGES: Record<CompileErrorCode, string> = {
    compile_in_progress: '该文档正在编译，请稍后再试',
    knowledge_base_busy: '知识库正在执行编译任务，请稍后再删除',
    compile_transaction_unavailable: '编译任务暂时无法创建，请稍后重试或联系管理员',
    recovery_required: '知识库正在恢复或需要管理员处理，暂不可用',
    llm_configuration: '模型服务暂不可用，请联系管理员检查配置',
    service_unavailable: '编译服务暂不可用，请稍后重试',
    timeout: '编译服务响应超时，请稍后重试',
    document_processing: '文档编译未完成，请检查文件内容后重试',
    compile_failed: '编译失败，请稍后重试或联系管理员',
    interrupted: '编译任务因服务重启中断，旧版本已恢复，请重新编译',
    rollback_failed: '编译失败，旧版本恢复异常，请联系管理员',
};

/** 已知错误码映射为固定文案;未知/缺失错误码回退到通用编译失败文案 */
export function getCompileErrorMessage(code?: string): string {
    return COMPILE_ERROR_MESSAGES[code as CompileErrorCode]
        ?? COMPILE_ERROR_MESSAGES.compile_failed;
}

/** 结构化 API 错误:仅携带安全文案、HTTP 状态和稳定机器码 */
export class ApiError extends Error {
    constructor(
        message: string,
        public readonly status: number,
        public readonly code?: string,
    ) {
        super(message);
        this.name = 'ApiError';
    }
}

/** 无 detail.code 时按状态码给出安全通用文案 */
function statusMessage(status: number): string {
    if (status === 404) return '未找到对应文档';
    if (status === 422) return '文件无法处理，请检查格式和内容后重试';
    if (status >= 500) return '服务暂不可用，请稍后重试';
    return '请求未完成，请稍后重试';
}

/**
 * 把任意捕获值映射为可展示给用户的文案:
 * ApiError 已含安全文案直接透传;网络层 TypeError 映射为固定网络文案;
 * 其余任意 Error 一律回退,不暴露原始 message(可能含密钥/堆栈)。
 */
export function getUserFacingErrorMessage(error: unknown, fallback: string): string {
    if (error instanceof ApiError) return error.message;
    if (error instanceof TypeError) return '网络连接异常，请检查连接后重试';
    return fallback;
}

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
    /** 文档终态编译/回滚失败的稳定机器错误码;展示文案由 getCompileErrorMessage 派生 */
    error_code?: DocumentCompileErrorCode;
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
    let payload: unknown;
    if (typeof res.text === 'function') {
        // 真实 Response:单次消费 body,JSON 解析失败按无 payload 处理
        const text = await res.text();
        if (text) {
            try {
                payload = JSON.parse(text);
            } catch {
                payload = undefined;
            }
        }
    } else if (typeof (res as { json?: unknown }).json === 'function') {
        // 兼容仅提供 json() 的轻量测试桩(真实 Response 总有 text())
        try {
            payload = await (res as unknown as { json: () => Promise<unknown> }).json();
        } catch {
            payload = undefined;
        }
    }
    if (!res.ok) {
        const detail = payload && typeof payload === 'object'
            ? (payload as { detail?: unknown }).detail
            : undefined;
        const code = detail && typeof detail === 'object'
            ? (detail as { code?: unknown }).code
            : undefined;
        const stableCode = typeof code === 'string' ? code : undefined;
        const message = stableCode
            ? getCompileErrorMessage(stableCode)
            : statusMessage(res.status);
        throw new ApiError(message, res.status, stableCode);
    }
    return payload as T;
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
