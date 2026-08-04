/**
 * tests/unit/api-errors.test.ts — 结构化 API 错误 TDD 测试 (E004 Task 6)
 * 验证:409 detail.code 解析、错误码→固定中文文案映射、
 * 不向用户暴露任意 Error.message、网络 TypeError 稳定映射。
 */

import {
    getCompileErrorMessage,
    getUserFacingErrorMessage,
    recompileDoc,
} from '@/lib/api';

const mockFetch = jest.fn();
const originalFetch = global.fetch;

beforeAll(() => {
    Object.defineProperty(global, 'fetch', {
        configurable: true,
        writable: true,
        value: mockFetch,
    });
});

beforeEach(() => {
    mockFetch.mockReset();
});

afterAll(() => {
    Object.defineProperty(global, 'fetch', {
        configurable: true,
        writable: true,
        value: originalFetch,
    });
});

describe('structured API errors', () => {
    test('parses compile_in_progress without exposing 409 or Conflict', async () => {
        mockFetch.mockResolvedValue({
            ok: false,
            status: 409,
            statusText: 'Conflict',
            text: async () => JSON.stringify({
                detail: { code: 'compile_in_progress' },
            }),
        } as unknown as Response);

        await expect(recompileDoc('doc_1')).rejects.toMatchObject({
            name: 'ApiError',
            status: 409,
            code: 'compile_in_progress',
            message: '该文档正在编译，请稍后再试',
        });
    });

    test.each([
        ['llm_configuration', '模型服务暂不可用，请联系管理员检查配置'],
        ['service_unavailable', '编译服务暂不可用，请稍后重试'],
        ['timeout', '编译服务响应超时，请稍后重试'],
        ['document_processing', '文档编译未完成，请检查文件内容后重试'],
        ['compile_failed', '编译失败，请稍后重试或联系管理员'],
        ['rollback_failed', '编译失败，旧版本恢复异常，请联系管理员'],
    ])('maps %s to fixed user copy', (code, expected) => {
        expect(getCompileErrorMessage(code)).toBe(expected);
    });

    test('does not expose an arbitrary Error.message', () => {
        const error = new Error('RuntimeError OPENAI_API_KEY=sk-secret API error 500');
        expect(getUserFacingErrorMessage(error, '上传失败，请稍后重试')).toBe(
            '上传失败，请稍后重试',
        );
    });

    test('maps network TypeError to a stable network message', () => {
        expect(getUserFacingErrorMessage(new TypeError('Failed to fetch'), 'fallback')).toBe(
            '网络连接异常，请检查连接后重试',
        );
    });
});
