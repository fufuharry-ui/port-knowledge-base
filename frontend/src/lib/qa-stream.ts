/**
 * src/lib/qa-stream.ts — SSE 流解析工具库
 * 将后端 /api/v1/qa 的混合 SSE 流解析为类型化事件对象
 */

export interface CitationMeta {
    ref: string;           // "[1]"
    doc_id: string;
    title?: string;
    section?: string;
}

export type QAEvent =
    | { type: 'thought'; step: number; message: string }
    | { type: 'source'; citations: CitationMeta[] }
    | { type: 'entity'; ids: string[] }
    | { type: 'delta'; text: string }
    | { type: 'done' };


/**
 * parseSSELine — 解析单行 SSE data 为 QAEvent
 * 返回 null 表示非数据行或解析失败
 */
export function parseSSELine(line: string): QAEvent | null {
    const trimmed = line.trim();
    if (!trimmed || !trimmed.startsWith('data:')) return null;

    const payload = trimmed.slice('data:'.length).trim();
    if (!payload || payload === '[DONE]') return null;

    try {
        const obj = JSON.parse(payload) as Record<string, unknown>;
        const t = obj.type as string;
        switch (t) {
            case 'thought':
                return { type: 'thought', step: Number(obj.step), message: String(obj.message) };
            case 'source':
                return { type: 'source', citations: (obj.citations as CitationMeta[]) ?? [] };
            case 'entity':
                return { type: 'entity', ids: (obj.ids as string[]) ?? [] };
            case 'delta':
                return { type: 'delta', text: String(obj.text ?? '') };
            case 'done':
                return { type: 'done' };
            default:
                return null;
        }
    } catch {
        return null;
    }
}


import { API_BASE } from './api';

export interface ChatTurn {
    role: 'user' | 'assistant';
    content: string;
}

/**
 * streamQA — 向后端发起 POST /api/v1/qa，返回 QAEvent 异步可迭代对象
 * Big-Loop #8: history 携带多轮对话,让后端解析追问代词。默认空(单轮,向后兼容)。
 */
export async function* streamQA(
    query: string,
    history: ChatTurn[] = [],
    apiBase = `${API_BASE}/api/v1`,
): AsyncIterable<QAEvent> {
    const response = await fetch(`${apiBase}/qa`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, history }),
    });

    if (!response.ok || !response.body) {
        yield { type: 'delta', text: `⚠️ 请求失败 (${response.status})` };
        yield { type: 'done' };
        return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
            const event = parseSSELine(line);
            if (event) yield event;
        }
    }

    // flush remaining buffer
    for (const line of buffer.split('\n')) {
        const event = parseSSELine(line);
        if (event) yield event;
    }
}
