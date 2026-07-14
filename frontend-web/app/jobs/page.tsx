'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { getDownloadUrl, getJob } from '@/lib/api';
import { TERMINAL_STATES } from '@/types';
import type { Clip, JobState, JobStatus } from '@/types';

const POLL_INTERVAL_MS = 2000;

function badgeClass(state: JobState): string {
  if (state === 'SUCCEEDED') return 'badge done';
  if (state === 'FAILED' || state === 'CANCELLED') return 'badge failed';
  return 'badge running';
}

function fmtTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function ClipRow({ jobId, clip }: { jobId: string; clip: Clip }) {
  const [busy, setBusy] = useState(false);

  async function handleDownload() {
    setBusy(true);
    try {
      const info = await getDownloadUrl(jobId, clip.clip_id);
      if (info.url) {
        window.open(info.url, '_blank', 'noopener');
      } else {
        // Mock mode / backend offline — no real artifact URL available.
        alert('（走路骨架）此為示範資料，尚無實際下載連結。後端就緒後即可下載。');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="clip">
      <div className="clip-head">
        <span className="clip-title">{clip.title || clip.clip_id}</span>
        {typeof clip.score === 'number' && (
          <span className="clip-score">{(clip.score * 100).toFixed(0)}</span>
        )}
      </div>
      <div className="clip-meta">
        <span className="mono">{clip.clip_id}</span> ·{' '}
        <span className="mono">
          {fmtTime(clip.start_sec)}–{fmtTime(clip.end_sec)}
        </span>{' '}
        （{(clip.end_sec - clip.start_sec).toFixed(0)} 秒）
      </div>
      {clip.reason && <p className="clip-reason">{clip.reason}</p>}
      <button className="secondary" onClick={handleDownload} disabled={busy}>
        {busy ? '取得連結中…' : '下載短片'}
      </button>
    </div>
  );
}

function JobView() {
  const params = useSearchParams();
  const jobId = params.get('id') ?? '';

  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let active = true;

    async function poll() {
      try {
        const data = await getJob(jobId);
        if (!active) return;
        setJob(data);
        setError(null);
        if (!TERMINAL_STATES.has(data.status)) {
          timerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        console.error(err);
        if (active) setError('查詢工作狀態失敗，請稍後重試。');
      }
    }

    poll();
    return () => {
      active = false;
      stopTimer();
    };
  }, [jobId, stopTimer]);

  if (!jobId) {
    return (
      <div className="card">
        <h1>找不到工作 ID</h1>
        <p className="hint">網址缺少 <span className="mono">?id=</span> 參數。</p>
        <div className="spacer">
          <Link href="/">← 回上傳頁</Link>
        </div>
      </div>
    );
  }

  const highlights = job?.highlights ?? [];
  const isDone = job?.status === 'SUCCEEDED';

  return (
    <main>
      <div className="card">
        <div className="clip-head">
          <h1 style={{ margin: 0 }}>工作狀態</h1>
          {job && <span className={badgeClass(job.status)}>{job.status}</span>}
        </div>
        <p className="clip-meta">
          Job ID：<span className="mono">{jobId}</span>
        </p>

        {!job && !error && <p className="hint">載入中…</p>}
        {error && <p className="error">{error}</p>}

        {job && (
          <>
            {job.current_stage && (
              <p className="hint" style={{ marginTop: 4 }}>
                目前階段：<span className="mono">{job.current_stage}</span>
              </p>
            )}
            {typeof job.progress === 'number' && (
              <>
                <div className="progress" aria-label="progress">
                  <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
                </div>
                <p className="hint">{job.progress}%</p>
              </>
            )}
            {job.status === 'FAILED' && (
              <p className="error">
                失敗：{job.error_code} {job.error_message}
              </p>
            )}
          </>
        )}
      </div>

      {isDone && (
        <div className="card">
          <h2>精彩高光（{highlights.length}）</h2>
          {highlights.length === 0 ? (
            <p className="hint">此工作沒有產生高光短片。</p>
          ) : (
            highlights.map((clip) => (
              <ClipRow key={clip.clip_id} jobId={jobId} clip={clip} />
            ))
          )}
        </div>
      )}

      <div className="spacer">
        <Link href="/">← 上傳另一支影片</Link>
      </div>
    </main>
  );
}

export default function JobsPage() {
  // useSearchParams requires a Suspense boundary under static export.
  return (
    <Suspense fallback={<div className="card"><p className="hint">載入中…</p></div>}>
      <JobView />
    </Suspense>
  );
}
