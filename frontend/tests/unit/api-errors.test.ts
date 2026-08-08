/**
 * tests/unit/api-errors.test.ts — 结构化 API 错误 TDD 测试 (E004 Task 6)
 * 验证:409 detail.code 解析、错误码→固定中文文案映射、
 * 不向用户暴露任意 Error.message、网络 TypeError 稳定映射。
 */

import {
    ApiError,
    deleteDoc,
    DocMeta,
    getCompileErrorMessage,
    getUserFacingErrorMessage,
    recompileDoc,
    uploadFile,
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
        ['knowledge_base_busy', '知识库正在执行编译任务，请稍后重试'],
        ['interrupted', '编译任务因服务重启中断，旧版本已恢复，请重新编译'],
        ['compile_transaction_unavailable', '编译任务暂时无法创建，请稍后重试或联系管理员'],
        ['recovery_required', '知识库正在恢复或需要管理员处理，暂不可用'],
    ])('maps %s to fixed user copy', (code, expected) => {
        expect(getCompileErrorMessage(code)).toBe(expected);
    });

    test('falls back to generic compile-failed copy for unknown or missing codes', () => {
        expect(getCompileErrorMessage('some_unknown_backend_code')).toBe(
            '编译失败，请稍后重试或联系管理员',
        );
        expect(getCompileErrorMessage(undefined)).toBe(
            '编译失败，请稍后重试或联系管理员',
        );
    });

    test('DocMeta.error_code accepts document terminal code interrupted', () => {
        const meta: DocMeta = { id: 'doc_1', status: 'error', error_code: 'interrupted' };
        expect(getCompileErrorMessage(meta.error_code)).toBe(
            '编译任务因服务重启中断，旧版本已恢复，请重新编译',
        );
    });

    test('maps 503 compile_transaction_unavailable to safe copy via handleResponse', async () => {
        mockFetch.mockResolvedValue({
            ok: false,
            status: 503,
            statusText: 'Service Unavailable',
            text: async () => JSON.stringify({
                detail: {
                    code: 'compile_transaction_unavailable',
                    job_id: 'job-9f3',
                    pid: 4242,
                    path: 'C:\\kb\\transactions\\job-9f3',
                },
            }),
        } as unknown as Response);

        const file = new File(['# Test'], 'test.md', { type: 'text/markdown' });
        await expect(uploadFile(file)).rejects.toMatchObject({
            name: 'ApiError',
            status: 503,
            code: 'compile_transaction_unavailable',
            message: '编译任务暂时无法创建，请稍后重试或联系管理员',
        });
    });

    test('maps 503 recovery_required to safe copy via handleResponse', async () => {
        mockFetch.mockResolvedValue({
            ok: false,
            status: 503,
            statusText: 'Service Unavailable',
            text: async () => JSON.stringify({
                detail: { code: 'recovery_required', job_id: 'job-77', pid: 1337 },
            }),
        } as unknown as Response);

        await expect(recompileDoc('doc_1')).rejects.toMatchObject({
            name: 'ApiError',
            status: 503,
            code: 'recovery_required',
            message: '知识库正在恢复或需要管理员处理，暂不可用',
        });
    });

    test('fixed copies never leak job_id, PID, paths, RuntimeError, status codes or env names', () => {
        const codes = [
            'compile_in_progress',
            'knowledge_base_busy',
            'llm_configuration',
            'service_unavailable',
            'timeout',
            'document_processing',
            'compile_failed',
            'interrupted',
            'rollback_failed',
            'compile_transaction_unavailable',
            'recovery_required',
        ];
        for (const code of codes) {
            const message = getCompileErrorMessage(code);
            expect(message).not.toMatch(/job_?id/i);
            expect(message).not.toMatch(/\bpid\b/i);
            expect(message).not.toMatch(/[A-Za-z]:[\\/]/); // Windows absolute path
            expect(message).not.toMatch(/[\\/]/); // any path separator
            expect(message).not.toMatch(/RuntimeError/i);
            expect(message).not.toMatch(/\b[45]\d\d\b/); // HTTP status codes
            expect(message).not.toMatch(/API_KEY|BASE_URL|MODEL_NAME|_MODEL\b/); // env var names
        }
        // ApiError passthrough carries only the mapped safe copy, not raw detail
        const err = new ApiError(
            getCompileErrorMessage('recovery_required'),
            503,
            'recovery_required',
        );
        expect(getUserFacingErrorMessage(err, 'fallback')).toBe(
            '知识库正在恢复或需要管理员处理，暂不可用',
        );
    });

    test('parses knowledge_base_busy on delete without exposing 409 or Conflict', async () => {
        mockFetch.mockResolvedValue({
            ok: false,
            status: 409,
            statusText: 'Conflict',
            text: async () => JSON.stringify({
                detail: { code: 'knowledge_base_busy' },
            }),
        } as unknown as Response);

        await expect(deleteDoc('doc_1')).rejects.toMatchObject({
            name: 'ApiError',
            status: 409,
            code: 'knowledge_base_busy',
            message: '知识库正在执行编译任务，请稍后重试',
        });
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
