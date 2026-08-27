"use client";

import React, { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Upload, X, FileText } from 'lucide-react';
import { formatBytes } from '@/lib/api';
import { MAX_FILES_PER_BATCH, MAX_UPLOAD_SIZE_BYTES, MAX_UPLOAD_SIZE_MB } from '@/lib/constants';
import { cn } from '@/lib/utils';

interface FileUploaderProps {
  onFilesSelected: (files: File[]) => void;
  maxFiles?: number;
  maxSize?: number;
}

export default function FileUploader({
  onFilesSelected,
  maxFiles = MAX_FILES_PER_BATCH,
  maxSize = MAX_UPLOAD_SIZE_BYTES,
}: FileUploaderProps) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);

  const onDrop = useCallback((acceptedFiles: File[]) => {
    const validFiles = acceptedFiles.filter((file) => {
      if (file.type !== 'application/pdf') {
        alert(`${file.name}은(는) PDF 파일이 아닙니다.`);
        return false;
      }
      if (file.size > maxSize) {
        alert(`${file.name}의 크기가 너무 큽니다 (최대 ${maxSize / (1024 * 1024)}MB).`);
        return false;
      }
      return true;
    });

    setSelectedFiles((prev) => [...prev, ...validFiles].slice(0, maxFiles));
  }, [maxFiles, maxSize]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf']
    },
    maxFiles: maxFiles - selectedFiles.length,
  });

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUpload = () => {
    if (selectedFiles.length > 0) {
      onFilesSelected(selectedFiles);
      setSelectedFiles([]);
    }
  };

  return (
    <div className="space-y-4">
      <div
        {...getRootProps()}
        className={cn(
          "border-2 border-dashed rounded-lg p-12 text-center cursor-pointer transition-colors",
          isDragActive
            ? "border-primary-500 bg-primary-50 dark:bg-primary-900/20"
            : "border-gray-300 hover:border-primary-400 dark:border-gray-600"
        )}
      >
        <input {...getInputProps()} />
        <Upload className="mx-auto h-12 w-12 text-gray-400 mb-4" />
        <p className="text-lg font-medium text-gray-700 dark:text-gray-300 mb-2">
          {isDragActive ? "파일을 여기에 놓으세요..." : "PDF 파일을 드래그하거나 클릭하세요"}
        </p>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          최대 {maxFiles}개 파일, 파일당 최대 {MAX_UPLOAD_SIZE_MB}MB
        </p>
      </div>

      {selectedFiles.length > 0 && (
        <div className="space-y-2">
          <h3 className="font-medium text-gray-900 dark:text-gray-100">
            선택된 파일 ({selectedFiles.length})
          </h3>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {selectedFiles.map((file, index) => (
              <div
                key={`${file.name}-${index}`}
                className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 rounded-lg"
              >
                <div className="flex items-center space-x-3 flex-1 min-w-0">
                  <FileText className="h-5 w-5 text-red-500 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                      {file.name}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {formatBytes(file.size)}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => removeFile(index)}
                  className="ml-2 p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded"
                  aria-label="파일 제거"
                >
                  <X className="h-4 w-4 text-gray-500" />
                </button>
              </div>
            ))}
          </div>
          <button
            onClick={handleUpload}
            className="w-full py-3 px-4 bg-primary-600 hover:bg-primary-700 text-white font-medium rounded-lg transition-colors"
          >
            업로드 시작
          </button>
        </div>
      )}
    </div>
  );
}
