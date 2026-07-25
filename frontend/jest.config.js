/** @type {import('jest').Config} */
const config = {
    testEnvironment: 'jsdom',
    setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
    transform: {
        '^.+\\.(ts|tsx)$': ['ts-jest', {
            tsconfig: {
                jsx: 'react-jsx',
            },
        }],
        '^.+\\.js$': 'babel-jest',
    },
    moduleNameMapper: {
        '^@/(.*)$': '<rootDir>/src/$1',
        '\\.(css|less|scss|sass)$': 'identity-obj-proxy',
        '\\.(png|jpg|jpeg|gif|svg|webp)$': '<rootDir>/__mocks__/fileMock.js',
        '^echarts$': '<rootDir>/__mocks__/echarts.js',
        '^echarts-for-react$': '<rootDir>/__mocks__/echartsForReact.js',
        // react-markdown v10 是纯 ESM,jest 无法加载 → 用轻量 mock(见 __mocks__/reactMarkdown.tsx)
        '^react-markdown$': '<rootDir>/__mocks__/reactMarkdown.tsx',
    },
    testMatch: [
        '<rootDir>/tests/unit/**/*.test.{ts,tsx}',
    ],
    testPathIgnorePatterns: ['/node_modules/', '/.next/'],
    collectCoverageFrom: ['src/**/*.{ts,tsx}'],
};

module.exports = config;
