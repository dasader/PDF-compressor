"use client";

import React from 'react';
import {
  FileText, CheckCircle, XCircle, Loader2, Clock,
  Download, Trash2, X as XIcon, TrendingDown
} from 'lucide-react';
import { Job, getDownloadUrl, formatBytes } from '@/lib/api';
import { cn } from '@/lib/utils';

interface JobCardProps {
  job: Job;
  onCancel?: (jobId: string) => void;
  onDelete?: (jobId: string) => void;
}

const statusConfig = {
  queued: {
    icon: Clock,
    color: 'text-gray-500',
    bgColor: 'bg-gray-100 dark:bg-gray-800',
    label: '대기 중'
  },
  running: {
    icon: Loader2,
    color: 'text-blue-500',
    bgColor: 'bg-blue-100 dark:bg-blue-900/30',
    label: '처리 중'
  },
  completed: {
    icon: CheckCircle,
    color: 'text-green-500',
    bgColor: 'bg-green-100 dark:bg-green-900/30',
    label: '완료'
  },
  failed: {
    icon: XCircle,
    color: 'text-red-500',
    bgColor: 'bg-red-100 dark:bg-red-900/30',
    label: '실패'
  },
  cancelled: {
    icon: XIcon,
    color: 'text-orange-500',
    bgColor: 'bg-orange-100 dark:bg-orange-900/30',
    label: '취소됨'
  }
};

const ACTION_CLASS =
  "flex-1 flex items-center justify-center space-x-2 py-2 px-4 text-white text-sm font-medium rounded transition-colors";

function StatRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-gray-600 dark:text-gray-400">{label}:</span>
      {children}
    </div>
  );
}

function JobCard({ job, onCancel, onDelete }: JobCardProps) {
  const config = statusConfig[job.status];
  const Icon = config.icon;

  const isProcessing = job.status === 'queued' || job.status === 'running';
  const isCompleted = job.status === 'completed';
  const isFailed = job.status === 'failed';

  return (
    <div className={cn("border rounded-lg p-4 transition-all", config.bgColor, "hover:shadow-md")}>
      {/* 헤더 */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center space-x-3 flex-1 min-w-0">
          <FileText className="h-6 w-6 text-red-500 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
              {job.original_filename}
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {formatBytes(job.original_size)}
            </p>
          </div>
        </div>

        <div className={cn("flex items-center space-x-2 px-2 py-1 rounded-full", config.bgColor)}>
          <Icon className={cn("h-4 w-4", config.color, job.status === 'running' && "animate-spin")} />
          <span className={cn("text-xs font-medium", config.color)}>
            {config.label}
          </span>
        </div>
      </div>

      {/* 진행률 */}
      {isProcessing && (
        <div className="mb-3">
          <div className="flex justify-between text-xs text-gray-600 dark:text-gray-400 mb-1">
            <span>진행률: {Math.round(job.progress * 100)}%</span>
          </div>
          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
            <div
              className="bg-primary-600 h-2 rounded-full transition-all duration-300"
              style={{ width: `${job.progress * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* 완료 정보 */}
      {isCompleted && (
        <div className="mb-3 space-y-2">
          <StatRow label="압축 후">
            <span className="font-medium text-gray-900 dark:text-gray-100">
              {formatBytes(job.compressed_size || 0)}
            </span>
          </StatRow>
          <StatRow label="압축률">
            <div className="flex items-center space-x-1">
              <TrendingDown className="h-4 w-4 text-green-500" />
              <span className="font-medium text-green-600 dark:text-green-400">
                {job.compression_percentage?.toFixed(1)}%
              </span>
            </div>
          </StatRow>
          <StatRow label="절약">
            <span className="font-medium text-gray-900 dark:text-gray-100">
              {formatBytes(job.saved_bytes || 0)}
            </span>
          </StatRow>
          {job.page_count && (
            <StatRow label="페이지 수">
              <span className="text-gray-900 dark:text-gray-100">{job.page_count}</span>
            </StatRow>
          )}
        </div>
      )}

      {/* 에러 메시지 */}
      {isFailed && job.error_message && (
        <div className="mb-3 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-400">
          {job.error_message}
        </div>
      )}

      {/* 액션 버튼 */}
      <div className="flex space-x-2">
        {isCompleted && (
          <a
            href={getDownloadUrl(job.id)}
            download
            className={cn(ACTION_CLASS, "bg-primary-600 hover:bg-primary-700")}
          >
            <Download className="h-4 w-4" />
            <span>다운로드</span>
          </a>
        )}

        {isProcessing && onCancel && (
          <button
            onClick={() => onCancel(job.id)}
            className={cn(ACTION_CLASS, "bg-orange-500 hover:bg-orange-600")}
          >
            <XIcon className="h-4 w-4" />
            <span>취소</span>
          </button>
        )}

        {(isCompleted || isFailed) && onDelete && (
          <button
            onClick={() => onDelete(job.id)}
            className="flex items-center justify-center space-x-2 py-2 px-4 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 text-sm font-medium rounded transition-colors"
            aria-label="작업 삭제"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

// SSE 진행률 이벤트마다 목록 전체가 리렌더되지 않도록 메모이즈
export default React.memo(JobCard);
