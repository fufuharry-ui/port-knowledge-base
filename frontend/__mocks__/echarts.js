// Mock for echarts in jest
module.exports = {
    init: jest.fn(() => ({
        setOption: jest.fn(),
        resize: jest.fn(),
        dispose: jest.fn(),
        on: jest.fn(),
        off: jest.fn(),
    })),
    graphic: { LinearGradient: jest.fn() },
};
