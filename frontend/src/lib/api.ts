/**
 * API 클라이언트
 */
import axios from 'axios';
import type { Preset, Engine } from './constants';

// 상대 경로를 사용하여 nginx/Next.js의 rewrites를 통해 프록시되도록 함
// 브라우저에서는 현재 호스트의 /api로 요청하고, nginx/Next.js가 백엔드로 프록시
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || '';

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 1800000, // 30분 (대용량 파일 업로드용)
});

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export const TERMINAL_STATUSES: readonly JobStatus[] = ['completed', 'failed', 'cancelled'];

export interface Job {
  id: string;
  filename: string;
  original_filename: string;
  status: JobStatus;
  progress: number;
  original_size: number;
  compressed_size?: number;
  compression_ratio?: number;
  compression_percentage?: number;
  saved_bytes?: number;
  page_count?: number;
  image_count?: number;
  error_message?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  expires_at?: string;
}

export interface UploadResponse {
  job_ids: string[];
  message: string;
}

export interface UploadOptions {
  preset?: Preset;
  engine?: Engine;
  preserve_metadata?: boolean;
  preserve_ocr?: boolean;
  user_session?: string;
}

// 파일 업로드
export const uploadFiles = async (
  files: File[],
  options: UploadOptions = {}
): Promise<UploadResponse> => {
  const formData = new FormData();

  files.forEach((file) => {
    formData.append('files', file);
  });

  formData.append('preset', options.preset || 'ebook');
  formData.append('engine', options.engine || 'ghostscript');
  formData.append('preserve_metadata', String(options.preserve_metadata ?? true));
  formData.append('preserve_ocr', String(options.preserve_ocr ?? true));

  if (options.user_session) {
    formData.append('user_session', options.user_session);
  }

  const response = await api.post<UploadResponse>('/api/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });

  return response.data;
};

// 작업 조회
export const getJob = async (jobId: string): Promise<Job> => {
  const response = await api.get<Job>(`/api/jobs/${jobId}`);
  return response.data;
};

// 작업 취소
export const cancelJob = async (jobId: string): Promise<void> => {
  await api.post(`/api/jobs/${jobId}/cancel`);
};

// 작업 삭제
export const deleteJob = async (jobId: string): Promise<void> => {
  await api.delete(`/api/jobs/${jobId}`);
};

// 파일 다운로드 URL 생성 (상대 경로)
export const getDownloadUrl = (jobId: string): string => `/api/jobs/${jobId}/download`;

// 일괄 다운로드
export const downloadBatch = async (jobIds: string[]): Promise<void> => {
  if (jobIds.length === 0) {
    throw new Error('다운로드할 작업이 없습니다');
  }

  const response = await api.post('/api/jobs/batch/download', jobIds, {
    responseType: 'blob',
  });

  // responseType: 'blob'이라 response.data가 이미 Blob — 다시 감싸면 메모리만 두 배로 쓴다
  const url = window.URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', 'compressed_files.zip');
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

// 포맷 유틸리티
export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
};
