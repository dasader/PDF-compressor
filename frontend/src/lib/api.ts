/**
 * API 클라이언트
 */
import axios from 'axios';

// 상대 경로를 사용하여 nginx/Next.js의 rewrites를 통해 프록시되도록 함
// 브라우저에서는 현재 호스트의 /api로 요청하고, nginx/Next.js가 백엔드로 프록시
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || '';

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 1800000, // 30분 (대용량 파일 업로드용)
});

export interface Job {
  id: string;
  filename: string;
  original_filename: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  eta_seconds?: number;
  original_size: number;
  compressed_size?: number;
  compression_ratio?: number;
  compression_percentage?: number;
  saved_bytes?: number;
  page_count?: number;
  image_count?: number;
  result_url?: string;
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

export interface HealthResponse {
  status: string;
  version: string;
  timestamp: string;
  redis_connected: boolean;
  worker_count: number;
}

// 파일 업로드
export const uploadFiles = async (
  files: File[],
  options: {
    preset?: string;
    engine?: string;
    preserve_metadata?: boolean;
    preserve_ocr?: boolean;
    user_session?: string;
  } = {}
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
  
  // multipart/form-data는 axios가 자동으로 설정하도록 Content-Type 헤더를 명시하지 않음
  const response = await api.post<UploadResponse>('/api/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// 작업 조회
export const getJob = async (jobId: string): Promise<Job> => {
  const response = await api.get<Job>(`/api/jobs/${jobId}`, {
    headers: {
      'Content-Type': 'application/json',
    },
  });
  return response.data;
};

// 작업 목록 조회
export const listJobs = async (
  userSession?: string,
  status?: string
): Promise<Job[]> => {
  const params: any = {};
  if (userSession) params.user_session = userSession;
  if (status) params.status = status;
  
  const response = await api.get<Job[]>('/api/jobs', { 
    params,
    headers: {
      'Content-Type': 'application/json',
    },
  });
  return response.data;
};

// 작업 취소
export const cancelJob = async (jobId: string): Promise<void> => {
  await api.post(`/api/jobs/${jobId}/cancel`, {}, {
    headers: {
      'Content-Type': 'application/json',
    },
  });
};

// 작업 삭제
export const deleteJob = async (jobId: string): Promise<void> => {
  await api.delete(`/api/jobs/${jobId}`, {
    headers: {
      'Content-Type': 'application/json',
    },
  });
};

// 파일 다운로드 URL 생성
export const getDownloadUrl = (jobId: string): string => {
  // 상대 경로 사용
  return `/api/jobs/${jobId}/download`;
};

// 일괄 다운로드
export const downloadBatch = async (jobIds: string[]): Promise<void> => {
  if (jobIds.length === 0) {
    throw new Error('다운로드할 작업이 없습니다');
  }

  const response = await api.post(
    '/api/jobs/batch/download',
    jobIds,
    {
      responseType: 'blob',
      headers: {
        'Content-Type': 'application/json',
      },
    }
  );
  
  // Blob을 다운로드 링크로 변환하여 다운로드
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', 'compressed_files.zip');
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

// 헬스체크
export const healthCheck = async (): Promise<HealthResponse> => {
  const response = await api.get<HealthResponse>('/api/healthz', {
    headers: {
      'Content-Type': 'application/json',
    },
  });
  return response.data;
};

// 포맷 유틸리티
export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
};

export const formatDuration = (seconds: number): string => {
  if (seconds < 60) return `${Math.round(seconds)}초`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}분`;
  return `${Math.round(seconds / 3600)}시간`;
};










