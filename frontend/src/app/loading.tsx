import { Spinner } from '@/components/ui/Spinner';

/** 全局路由加载边界(页面级骨架屏已内嵌各页,此处兜底路由切换) */
export default function Loading() {
    return (
        <div className="flex min-h-[40vh] items-center justify-center" role="status" aria-label="页面加载中">
            <Spinner size={24} />
        </div>
    );
}
