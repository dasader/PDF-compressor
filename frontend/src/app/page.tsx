"use client";

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FileDown, Info } from 'lucide-react';
import FileUploader from '@/components/FileUploader';
import JobCard from '@/components/JobCard';
import SettingsPanel, { type CompressionSettings } from '@/components/SettingsPanel';
import { uploadFiles, getJob, cancelJob, deleteJob, downloadBatch, Job } from '@/lib/api';
import { APP_NAME, MAX_UPLOAD_SIZE_MB, RETENTION_HOURS, isTerminal } from '@/lib/constants';
import { subscribeJob } from '@/lib/sse';
import { showApiError } from '@/lib/utils';

export default function Home() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [settings, setSettings] = useState<CompressionSettings>({
    preset: 'ebook',
    engine: 'ghostscript',
    preserveMetadata: true,
  });
  const [userSession] = useState(() => {
    if (typeof window === 'undefined') return '';
    let session = localStorage.getItem('userSession');
    if (!session) {
      session = `session_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`;
      localStorage.setItem('userSession', session);
    }
    return session;
  });

  const subscriptions = useRef(new Map<string, () => void>());

  const applyPartial = useCallback((partial: Partial<Job> & { job_id: string }) => {
    setJobs((prev) => prev.map((j) => (j.id === partial.job_id ? { ...j, ...partial } : j)));
  }, []);

  const refreshJob = useCallback((jobId: string) => {
    // terminal 상태 도달 시 최종 전체 상태를 1회 재조회해 누락된 필드 보정
    getJob(jobId)
      .then((full) => setJobs((prev) => prev.map((j) => (j.id === jobId ? full : j))))
      .catch(() => {});
  }, []);

  // 활성 작업만 개별 구독 — 한 작업의 상태 변화가 다른 작업의 연결을 끊지 않는다
  useEffect(() => {
    const active = new Set(jobs.filter((j) => !isTerminal(j.status)).map((j) => j.id));
    const subs = subscriptions.current;

    subs.forEach((unsubscribe, id) => {
      if (!active.has(id)) {
        unsubscribe();
        subs.delete(id);
      }
    });

    active.forEach((id) => {
      if (!subs.has(id)) {
        subs.set(id, subscribeJob(id, applyPartial, () => refreshJob(id)));
      }
    });
  }, [jobs, applyPartial, refreshJob]);

  // 언마운트 시 남은 구독 정리
  useEffect(() => {
    const subs = subscriptions.current;
    return () => {
      subs.forEach((unsubscribe) => unsubscribe());
      subs.clear();
    };
  }, []);

  const handleFilesSelected = useCallback(async (files: File[]) => {
    try {
      const response = await uploadFiles(files, {
        preset: settings.preset,
        engine: settings.engine,
        preserve_metadata: settings.preserveMetadata,
        user_session: userSession,
      });

      // 서버가 생성된 Job을 그대로 돌려주므로 파일당 재조회가 필요 없다
      setJobs((prev) => [...response.jobs, ...prev]);

      const failures = response.failed
        .map((f) => `\n· ${f.filename}: ${f.error}`)
        .join('');
      alert(response.message + failures);
    } catch (error) {
      showApiError('업로드 실패', error);
    }
  }, [settings, userSession]);

  const handleCancelJob = useCallback(async (jobId: string) => {
    try {
      await cancelJob(jobId);
      refreshJob(jobId);
    } catch (error) {
      showApiError('작업 취소 실패', error);
    }
  }, [refreshJob]);

  const handleDeleteJob = useCallback(async (jobId: string) => {
    try {
      await deleteJob(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (error) {
      showApiError('작업 삭제 실패', error);
    }
  }, []);

  const handleDownloadAll = useCallback(async () => {
    const completedIds = jobs.filter((job) => job.status === 'completed').map((job) => job.id);

    if (completedIds.length === 0) {
      alert('다운로드할 완료된 작업이 없습니다.');
      return;
    }

    try {
      await downloadBatch(completedIds);
    } catch (error) {
      showApiError('일괄 다운로드 실패', error);
    }
  }, [jobs]);

  const handleSettingsChange = useCallback((patch: Partial<CompressionSettings>) => {
    setSettings((prev) => ({ ...prev, ...patch }));
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* 헤더 */}
      <header className="bg-white dark:bg-gray-800 shadow">
        <div className="container mx-auto px-4 py-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <FileDown className="h-8 w-8 text-primary-600" />
              <div>
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                  {APP_NAME}
                </h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  대용량 PDF 파일 압축 도구
                </p>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* 메인 컨텐츠 */}
      <main className="container mx-auto px-4 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* 왼쪽: 업로드 & 작업 목록 */}
          <div className="lg:col-span-2 space-y-6">
            {/* 업로드 영역 */}
            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-md p-6">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
                파일 업로드
              </h2>
              <FileUploader onFilesSelected={handleFilesSelected} />
            </div>

            {/* 정보 패널 */}
            <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
              <div className="flex items-start space-x-3">
                <Info className="h-5 w-5 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
                <div className="text-sm text-blue-800 dark:text-blue-200">
                  <p className="font-medium mb-1">사용 안내</p>
                  <ul className="list-disc list-inside space-y-1 text-xs">
                    <li>최대 {MAX_UPLOAD_SIZE_MB}MB까지의 PDF 파일을 업로드할 수 있습니다</li>
                    <li>여러 파일을 동시에 업로드하면 순차적으로 처리됩니다</li>
                    <li>압축된 파일은 {RETENTION_HOURS}시간 동안 보관됩니다</li>
                    <li>암호화된 PDF는 지원하지 않습니다</li>
                  </ul>
                </div>
              </div>
            </div>

            {/* 작업 목록 */}
            {jobs.length > 0 && (
              <div className="bg-white dark:bg-gray-800 rounded-lg shadow-md p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                    작업 목록 ({jobs.length})
                  </h2>
                  {jobs.some((job) => job.status === 'completed') && (
                    <button
                      onClick={handleDownloadAll}
                      className="flex items-center space-x-2 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm font-medium rounded transition-colors"
                    >
                      <FileDown className="h-4 w-4" />
                      <span>모두 다운로드</span>
                    </button>
                  )}
                </div>
                <div className="space-y-4">
                  {jobs.map((job) => (
                    <JobCard
                      key={job.id}
                      job={job}
                      onCancel={handleCancelJob}
                      onDelete={handleDeleteJob}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 오른쪽: 설정 패널 */}
          <div className="lg:col-span-1">
            <SettingsPanel settings={settings} onChange={handleSettingsChange} />
          </div>
        </div>
      </main>

      {/* 푸터 */}
      <footer className="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 mt-12">
        <div className="container mx-auto px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400">
          <p>© 2025 {APP_NAME}. 모든 권리 보유.</p>
          <p className="mt-1">
            Ghostscript와 pikepdf를 사용합니다.
          </p>
        </div>
      </footer>
    </div>
  );
}
