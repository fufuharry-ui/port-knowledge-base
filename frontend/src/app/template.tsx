/** 页面切换模板:每次导航重新挂载,触发 fade-in 过渡(prefers-reduced-motion 下自动禁用) */
export default function Template({ children }: { children: React.ReactNode }) {
    return <div className="fade-in">{children}</div>;
}
