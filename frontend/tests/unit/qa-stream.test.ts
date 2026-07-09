/**
 * tests/unit/qa-stream.test.ts — SSE 流解析工具库 TDD 测试 (Phase E)
 * 验证 parseSSELine() 对各种事件类型的解析正确性
 */

import { parseSSELine, QAEvent } from '@/lib/qa-stream';

describe('parseSSELine', () => {
    test('parses thought event correctly', () => {
        const line = 'data: {"type":"thought","step":1,"message":"命中本体关键词：[岸桥]"}';
        const result = parseSSELine(line);

        expect(result).not.toBeNull();
        expect(result!.type).toBe('thought');
        if (result!.type === 'thought') {
            expect(result!.step).toBe(1);
            expect(result!.message).toContain('岸桥');
        }
    });

    test('parses source event with citations array', () => {
        const citations = [
            { ref: '[1]', doc_id: 'doc_001', title: '岸桥远控技术方案', section: '第3章' }
        ];
        const line = `data: ${JSON.stringify({ type: 'source', citations })}`;
        const result = parseSSELine(line);

        expect(result).not.toBeNull();
        expect(result!.type).toBe('source');
        if (result!.type === 'source') {
            expect(result!.citations).toHaveLength(1);
            expect(result!.citations[0].ref).toBe('[1]');
            expect(result!.citations[0].doc_id).toBe('doc_001');
        }
    });

    test('parses entity event with ids array', () => {
        const line = 'data: {"type":"entity","ids":["doc_001","doc_002"]}';
        const result = parseSSELine(line);

        expect(result).not.toBeNull();
        expect(result!.type).toBe('entity');
        if (result!.type === 'entity') {
            expect(result!.ids).toEqual(['doc_001', 'doc_002']);
        }
    });

    test('parses delta event with text', () => {
        const line = 'data: {"type":"delta","text":"根据技术方案，延迟≤50ms"}';
        const result = parseSSELine(line);

        expect(result).not.toBeNull();
        expect(result!.type).toBe('delta');
        if (result!.type === 'delta') {
            expect(result!.text).toBe('根据技术方案，延迟≤50ms');
        }
    });

    test('parses done event', () => {
        const line = 'data: {"type":"done"}';
        const result = parseSSELine(line);

        expect(result).not.toBeNull();
        expect(result!.type).toBe('done');
    });

    test('returns null for empty line', () => {
        expect(parseSSELine('')).toBeNull();
        expect(parseSSELine('  ')).toBeNull();
    });

    test('returns null for non-data lines (event: / id: / comment)', () => {
        expect(parseSSELine('event: thought')).toBeNull();
        expect(parseSSELine(': keep-alive')).toBeNull();
        expect(parseSSELine('id: 42')).toBeNull();
    });

    test('handles malformed JSON gracefully — returns null', () => {
        const line = 'data: {not valid json}';
        expect(() => parseSSELine(line)).not.toThrow();
        expect(parseSSELine(line)).toBeNull();
    });

    test('handles [DONE] sentinel gracefully', () => {
        const line = 'data: [DONE]';
        const result = parseSSELine(line);
        expect(result).toBeNull();
    });

    test('parses thought event step 2', () => {
        const line = 'data: {"type":"thought","step":2,"message":"Layer2 精选 2 篇文档..."}';
        const result = parseSSELine(line);

        expect(result).not.toBeNull();
        if (result!.type === 'thought') {
            expect(result!.step).toBe(2);
        }
    });
});
