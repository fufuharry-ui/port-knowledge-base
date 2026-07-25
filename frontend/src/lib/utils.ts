import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** cn() — clsx + tailwind-merge,组件样式组合的标准入口 */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}
