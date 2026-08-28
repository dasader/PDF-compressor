/**
 * 백엔드 설정과 맞춰야 하는 값들 — 여기 한 곳만 고친다.
 * ponytail: 백엔드 Settings와 수동 동기화. 어긋나서 문제가 생기면 /api/config 엔드포인트로 승격.
 */
export const APP_NAME = 'PDF Compressor(made by mesmerized!)';

export const MAX_FILES_PER_BATCH = 10;
export const MAX_UPLOAD_SIZE_MB = 512;
export const MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024;
export const RETENTION_HOURS = 24;

export const PRESETS = [
  { value: 'screen', label: '최대 압축 (Screen)', description: '72 DPI, 화면 보기용' },
  { value: 'ebook', label: '기본 (E-book)', description: '150 DPI, 전자책용 (권장)' },
  { value: 'printer', label: '균형 (Printer)', description: '300 DPI, 인쇄용' },
  { value: 'prepress', label: '고품질 (Prepress)', description: '300 DPI, 고품질 인쇄' },
] as const;

export const ENGINES = [
  {
    value: 'ghostscript',
    label: '최대 압축 (Ghostscript)',
    description: '스캔 문서 권장 · 이미지 해상도를 낮춰 화질이 일부 떨어집니다',
  },
  {
    value: 'pikepdf',
    label: '무손실 (pikepdf)',
    description: '원본 화질 유지 · 텍스트 문서에 효과적이며 스캔본은 거의 줄지 않습니다',
  },
] as const;

export type Preset = (typeof PRESETS)[number]['value'];
export type Engine = (typeof ENGINES)[number]['value'];

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export const TERMINAL_STATUSES: readonly JobStatus[] = ['completed', 'failed', 'cancelled'];

export const isTerminal = (status: JobStatus) => TERMINAL_STATUSES.includes(status);
