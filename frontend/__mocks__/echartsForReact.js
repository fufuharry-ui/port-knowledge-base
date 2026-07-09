// Mock for echarts-for-react in jest
const React = require('react');

const ReactECharts = React.forwardRef(({ 'data-testid': testId, option, 'data-highlight-count': highlightCount }, ref) => {
    return React.createElement('div', {
        'data-testid': testId || 'echarts-mock',
        'data-nodes': option?.series?.[0]?.data?.length ?? 0,
        'data-highlight-count': highlightCount ?? 0,
    });
});

ReactECharts.displayName = 'MockReactECharts';
module.exports = { __esModule: true, default: ReactECharts };
