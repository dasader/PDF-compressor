import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** API 에러를 콘솔에 남기고 사용자에게 알린다. */
export function showApiError(prefix: string, error: unknown) {
  const detail =
    (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
    (error as Error)?.message ??
    "알 수 없는 오류";
  console.error(`${prefix}:`, error);
  alert(`${prefix}: ${detail}`);
}
