"use client";

import React, { useState, useEffect } from 'react';
import { FileDown, Info } from 'lucide-react';
import FileUploader from '@/components/FileUploader';
import JobCard from '@/components/JobCard';
import SettingsPanel from '@/components/SettingsPanel';
import { uploadFiles, getJob, cancelJob, deleteJob, downloadBatch, Job } from '@/lib/api';
import { subscribeJob } from '@/lib/sse';

export default function Home() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [preset, setPreset] = useState('ebook');
  const [engine, setEngine] = useState('ghostscript');
  const [preserveMetadata, setPreserveMetadata] = useState(true);
  const [preserveOcr, setPreserveOcr] = useState(true);
  const [userSession] = useState(() => {
    if (typeof window !== 'undefined') {
      let session = localStorage.getItem('userSession');
      if (!session) {
        session = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        localStorage.setItem('userSession', session);
      }
      return session;
    }
    return '';
  });

  // 활성 작업에 대한 SSE 구독
  useEffect(() => {
    const activeJobs = jobs.filter(
      (job) => job.status === 'queued' || job.status === 'running'
    );
    if (activeJobs.length === 0) return;

    const unsubscribes = activeJobs.map((job) =>
      subscribeJob(
        job.id,
        (partial) => {
          setJobs((prev) =>
            prev.map((j) => (j.id === partial.id ? { ...j, ...partial } : j))
          );
        },
        () => {
          // terminal 상태 도달 시 최종 전체 상태를 1회 재조회해 누락된 필드 보정
          getJob(job.id)
            .then((full) => {
              setJobs((prev) => prev.map((j) => (j.id === job.id ? full : j)));
            })
            .catch(() => {});
        }
      )
    );

    return () => {
      unsubscribes.forEach((u) => u());
    };
    // 활성 job id+status 집합 변화에만 재구독
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.map((j) => `${j.id}:${j.status}`).join(',')]);

  const handleFilesSelected = async (files: File[]) => {
    try {
      const response = await uploadFiles(files, {
        preset,
        engine,
        preserve_metadata: preserveMetadata,
        preserve_ocr: preserveOcr,
        user_session: userSession,
      });

      // 새 작업 추가
      const newJobs = await Promise.all(
        response.job_ids.map((id) => getJob(id))
      );
      
      setJobs((prev) => [...newJobs, ...prev]);
      
      alert(`${files.length}개 파일 업로드 완료!`);
    } catch (error: any) {
      console.error('업로드 실패:', error);
      alert(`업로드 실패: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleCancelJob = async (jobId: string) => {
    try {
      await cancelJob(jobId);
      const updatedJob = await getJob(jobId);
      setJobs((prev) => prev.map((j) => (j.id === jobId ? updatedJob : j)));
    } catch (error) {
      console.error('작업 취소 실패:', error);
      alert('작업 취소에 실패했습니다.');
    }
  };

  const handleDeleteJob = async (jobId: string) => {
    try {
      await deleteJob(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (error) {
      console.error('작업 삭제 실패:', error);
      alert('작업 삭제에 실패했습니다.');
    }
  };

  const handleRetryJob = async (jobId: string) => {
    // 재시도는 동일한 파일을 다시 업로드해야 하므로 여기서는 간단히 처리
    alert('재시도 기능은 파일을 다시 업로드해주세요.');
  };

  const handleDownloadAll = async () => {
    const completedJobs = jobs.filter((job) => job.status === 'completed');
    
    if (completedJobs.length === 0) {
      alert('다운로드할 완료된 작업이 없습니다.');
      return;
    }

    try {
      const jobIds = completedJobs.map((job) => job.id);
      await downloadBatch(jobIds);
    } catch (error: any) {
      console.error('일괄 다운로드 실패:', error);
      alert(`다운로드 실패: ${error.response?.data?.detail || error.message}`);
    }
  };

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
                  PDF Compressor(made by mesmerized!)
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
                    <li>최대 512MB까지의 PDF 파일을 업로드할 수 있습니다</li>
                    <li>여러 파일을 동시에 업로드하면 순차적으로 처리됩니다</li>
                    <li>압축된 파일은 24시간 동안 보관됩니다</li>
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
                      onRetry={handleRetryJob}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 오른쪽: 설정 패널 */}
          <div className="lg:col-span-1">
            <SettingsPanel
              preset={preset}
              setPreset={setPreset}
              engine={engine}
              setEngine={setEngine}
              preserveMetadata={preserveMetadata}
              setPreserveMetadata={setPreserveMetadata}
              preserveOcr={preserveOcr}
              setPreserveOcr={setPreserveOcr}
            />
          </div>
        </div>
      </main>

      {/* 푸터 */}
      <footer className="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 mt-12">
        <div className="container mx-auto px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400">
          <p>© 2025 PDF Compressor(made by mesmerized!). 모든 권리 보유.</p>
          <p className="mt-1">
            Ghostscript, qpdf, pikepdf를 사용합니다.
          </p>
        </div>
      </footer>
    </div>
  );
}











