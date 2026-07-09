require('@testing-library/jest-dom');

// Mock Next.js router (plain CommonJS, no TypeScript)
jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: jest.fn(),
        replace: jest.fn(),
        prefetch: jest.fn(),
        back: jest.fn(),
        refresh: jest.fn(),
        pathname: '/',
    }),
    useSearchParams: () => new URLSearchParams(),
    usePathname: () => '/',
}));

// Mock Next.js Image
jest.mock('next/image', () => ({
    __esModule: true,
    default: function MockImage(props) {
        var React = require('react');
        return React.createElement('img', props);
    },
}));
