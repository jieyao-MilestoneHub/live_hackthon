'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createJob } from '@/lib/api';

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) {
      setError('請先選擇一個影片檔案。');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const created = await createJob({
        filename: file.name,
        content_type: file.type || 'video/mp4',
      });

      // TODO(team, #16): 走路骨架僅建立 job 後直接導向狀態頁。
      // 後續：用 created.upload 的 presigned URL 執行真正的 S3 multipart 上傳，
      // 將 file 的 bytes 以 PUT 上傳到 created.upload.url（method/key 依後端回傳）。
      // 上傳完成後再通知後端開始處理（依契約補上對應端點）。

      router.push(`/jobs?id=${encodeURIComponent(created.job_id)}`);
    } catch (err) {
      console.error(err);
      setError('建立工作失敗，請稍後再試。');
      setSubmitting(false);
    }
  }

  return (
    <main>
      <div className="card">
        <h1>上傳直播影片</h1>
        <p className="subtitle" style={{ marginBottom: 20 }}>
          選擇一段直播錄影，系統會自動分析並剪出精彩高光短片。
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="video">影片檔案（mp4 等）</label>
          <input
            id="video"
            type="file"
            accept="video/*"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />

          {file && (
            <p className="hint">
              已選擇：<span className="mono">{file.name}</span>
              {file.size > 0 && (
                <> · {(file.size / (1024 * 1024)).toFixed(1)} MB</>
              )}
            </p>
          )}

          <div className="row spacer">
            <button type="submit" disabled={submitting || !file}>
              {submitting ? '建立工作中…' : '建立高光剪輯工作'}
            </button>
          </div>

          {error && <p className="error">{error}</p>}
        </form>

        <p className="hint">
          走路骨架階段：僅建立 job 後導向狀態頁，尚未執行真正的 S3
          上傳（見程式碼 TODO）。若後端未啟動，會自動使用本地 mock
          資料以便預覽 UI。
        </p>
      </div>
    </main>
  );
}
