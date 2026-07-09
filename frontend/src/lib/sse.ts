/**
 * src/lib/sse.ts — SSE 流式检索客户端
 * 解析 text/event-stream 响应，逐 chunk 回调
 */
import { buildStreamUrl } from './api';

export interface SSECallbacks {
    onDelta: (text: string) => void;
    onThought?: (step: number, message: string) => void;
    onDone: () => void;
    onError: (err: Error) => void;
}

/**
 * 开启 SSE 流式检索
 * @returns AbortController — 调用 .abort() 可取消
 */
export function startStreamSearch(query: string, callbacks: SSECallbacks): AbortController {
    const controller = new AbortController();
    const url = buildStreamUrl(query);

    (async () => {
        try {
            const res = await fetch(url, { signal: controller.signal });
            if (!res.ok || !res.body) {
                throw new Error(`Stream error ${res.status}`);
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split('\n');
                buffer = lines.pop() ?? ''; // 保留未完成的行

                for (const line of lines) {
                    if (!line.startsWith('data:')) continue;
                    const payload = line.slice(5).trim();
                    if (payload === '[DONE]') {
                        callbacks.onDone();
                        return;
                    }
                    try {
                        const parsed = JSON.parse(payload);
                        if (parsed.type === 'thought' && callbacks.onThought) {
                            callbacks.onThought(parsed.step, parsed.message);
                        } else if (parsed.delta) {
                            callbacks.onDelta(parsed.delta);
                        }
                    } catch {
                        // 忽略无效 JSON
                    }
                }
            }
            callbacks.onDone();
        } catch (err) {
            if ((err as Error).name === 'AbortError') return;
            callbacks.onError(err instanceof Error ? err : new Error(String(err)));
        }
    })();

    return controller;
}
