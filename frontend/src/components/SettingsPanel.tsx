"use client";

import React from 'react';
import { Settings } from 'lucide-react';
import { PRESETS, ENGINES, type Preset, type Engine } from '@/lib/constants';

export interface CompressionSettings {
  preset: Preset;
  engine: Engine;
  preserveMetadata: boolean;
  preserveOcr: boolean;
}

interface SettingsPanelProps {
  settings: CompressionSettings;
  onChange: (patch: Partial<CompressionSettings>) => void;
}

function RadioGroup<T extends string>({
  title,
  name,
  options,
  selected,
  onSelect,
}: {
  title: string;
  name: string;
  options: readonly { value: T; label: string; description: string }[];
  selected: T;
  onSelect: (value: T) => void;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
        {title}
      </label>
      <div className="space-y-2">
        {options.map((option) => (
          <label
            key={option.value}
            className="flex items-start space-x-3 p-3 border rounded-lg cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={selected === option.value}
              onChange={() => onSelect(option.value)}
              className="mt-1"
            />
            <div>
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                {option.label}
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400">
                {option.description}
              </div>
            </div>
          </label>
        ))}
      </div>
    </div>
  );
}

function Checkbox({
  checked,
  onToggle,
  children,
}: {
  checked: boolean;
  onToggle: (value: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-center space-x-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(e.target.checked)}
        className="rounded"
      />
      <span className="text-sm text-gray-700 dark:text-gray-300">{children}</span>
    </label>
  );
}

export default function SettingsPanel({ settings, onChange }: SettingsPanelProps) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-md p-6">
      <div className="flex items-center space-x-2 mb-4">
        <Settings className="h-5 w-5 text-gray-700 dark:text-gray-300" />
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          압축 설정
        </h2>
      </div>

      <div className="space-y-6">
        <RadioGroup
          title="압축 프리셋"
          name="preset"
          options={PRESETS}
          selected={settings.preset}
          onSelect={(preset) => onChange({ preset })}
        />

        <RadioGroup
          title="압축 엔진"
          name="engine"
          options={ENGINES}
          selected={settings.engine}
          onSelect={(engine) => onChange({ engine })}
        />

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            고급 옵션
          </label>
          <div className="space-y-2">
            <Checkbox
              checked={settings.preserveMetadata}
              onToggle={(preserveMetadata) => onChange({ preserveMetadata })}
            >
              메타데이터 보존 (저작권, 태그 등)
            </Checkbox>
            <Checkbox
              checked={settings.preserveOcr}
              onToggle={(preserveOcr) => onChange({ preserveOcr })}
            >
              OCR 텍스트 레이어 보존 (스캔 PDF)
            </Checkbox>
          </div>
        </div>
      </div>
    </div>
  );
}
