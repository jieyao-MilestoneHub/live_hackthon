'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createProject } from '@/lib/api';

const MAX_TARGET_SEC = 60;
const DEFAULT_TARGET_SEC = 30;

export default function CreateProjectPage() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [targetSec, setTargetSec] = useState(DEFAULT_TARGET_SEC);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (targetSec < 1 || targetSec > MAX_TARGET_SEC) {
      setError(`目標秒數需介於 1–${MAX_TARGET_SEC} 秒。`);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject({
        title: title.trim() || undefined,
        target_duration_ms: Math.round(targetSec * 1000),
      });
      router.push(`/projects?id=${encodeURIComponent(created.project_id)}`);
    } catch (err) {
      console.error(err);
      setError('建立 Project 失敗，請稍後再試。');
      setSubmitting(false);
    }
  }

  return (
    <main>
      <div className="card">
        <h1>建立影片專案</h1>
        <p className="subtitle" style={{ marginBottom: 20 }}>
          指定最終短片的目標長度（上限 {MAX_TARGET_SEC} 秒），接著上傳原始直播影片，
          系統會分析高光並建立初始剪輯草稿。
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="title">專案標題（選填）</label>
          <input
            id="title"
            type="text"
            placeholder="例如：巔峰對決精華"
            value={title}
            maxLength={80}
            onChange={(e) => setTitle(e.target.value)}
          />

          <div className="spacer">
            <label htmlFor="target">目標秒數（1–{MAX_TARGET_SEC} 秒）</label>
            <div className="row">
              <input
                id="target"
                type="range"
                min={1}
                max={MAX_TARGET_SEC}
                value={targetSec}
                onChange={(e) => setTargetSec(Number(e.target.value))}
                style={{ flex: 1 }}
              />
              <input
                type="number"
                min={1}
                max={MAX_TARGET_SEC}
                value={targetSec}
                onChange={(e) => setTargetSec(Number(e.target.value))}
                className="num-input"
                aria-label="目標秒數"
              />
              <span className="mono muted">秒</span>
            </div>
            <p className="hint">
              = <span className="mono">{Math.round(targetSec * 1000)}</span> ms
              （契約以毫秒為單位）
            </p>
          </div>

          <div className="row spacer">
            <button type="submit" disabled={submitting}>
              {submitting ? '建立中…' : '建立專案並前往上傳'}
            </button>
          </div>

          {error && <p className="error">{error}</p>}
        </form>

        <p className="hint">
          建立後將導向編輯器（<span className="mono">/projects?id=…</span>）。
          若後端未啟動，會自動使用本地 mock 資料以便預覽 UI。
        </p>
      </div>
    </main>
  );
}
