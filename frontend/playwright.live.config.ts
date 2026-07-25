import { defineConfig, devices } from '@playwright/test';
import path from 'path';

const projectRoot = path.resolve(__dirname, '..');
const python = process.env.UAT_PYTHON ?? 'D:\\ProgramData\\anaconda3\\python.exe';

export default defineConfig({
    testDir: './tests/e2e-live',
    fullyParallel: false,
    workers: 1,
    retries: 0,
    timeout: 180_000,
    expect: { timeout: 15_000 },
    reporter: [
        ['list'],
        ['json', { outputFile: '../UAT测试日志/20260719/evidence/playwright-live.json' }],
    ],
    use: {
        baseURL: 'http://127.0.0.1:3001',
        trace: 'retain-on-failure',
        screenshot: 'only-on-failure',
    },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    webServer: [
        {
            command: `"${python}" tests/uat/live_server.py --port 8001`,
            cwd: projectRoot,
            url: 'http://127.0.0.1:8001/api/v1/health',
            reuseExistingServer: false,
            timeout: 120_000,
        },
        {
            command: 'npm run dev -- --port 3001',
            cwd: __dirname,
            env: { NEXT_PUBLIC_API_BASE: 'http://127.0.0.1:8001' },
            url: 'http://127.0.0.1:3001/wiki',
            reuseExistingServer: false,
            timeout: 120_000,
        },
    ],
});
