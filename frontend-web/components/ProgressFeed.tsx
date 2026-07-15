'use client';

import { useEffect, useRef, useState } from 'react';
import { getProgress } from '@/lib/api';
import type { ProgressView } from '@/types';

const POLL_MS = 2000;

function hhmmss(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString('zh-TW', { hour12: false });
}

/**
 * Live, AI-synthesized progress feed shown while the pipeline is working.
 * Polls GET /projects/{id}/progress every 2s while `active`, renders the latest
 * narration prominently plus a chronological log — so production is never a
 * black box. No-op (renders nothing) until the first event arrives.
 */
export default function ProgressFeed({
  projectId,
  active,
}: {
  projectId: string;
  active: boolean;
}) {
  const [view, setView] = useState<ProgressView | null>(null);
  const logRef = useRef<HTMLUListElement | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    const tick = () =>
      getProgress(projectId)
        .then((v) => alive && setView(v))
        .catch(() => {});
    tick(); // fetch immediately, then poll while active
    if (!active) return () => {
      alive = false;
    };
    const timer = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [projectId, active]);

  // Keep the newest line in view as the feed grows.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [view?.events.length]);

  const events = view?.events ?? [];
  const latest = view?.latest ?? null;
  if (!latest) return null;

  const running = latest.status === 'RUNNING' && active;

  return (
    <div className="progress-feed" style={{ marginTop: 8 }}>
      <div
        className="progress-feed__latest"
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <span
          aria-hidden
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            flex: '0 0 auto',
            background: running ? '#e0218a' : '#22c55e',
            boxShadow: running ? '0 0 0 0 rgba(224,33,138,0.6)' : 'none',
            animation: running ? 'pulse 1.4s ease-out infinite' : 'none',
          }}
        />
        <strong className="cjk" style={{ fontSize: 15, lineHeight: 1.4 }}>
          {latest.message}
        </strong>
      </div>

      {events.length > 1 && (
        <ul
          ref={logRef}
          className="progress-feed__log"
          style={{
            listStyle: 'none',
            margin: '10px 0 0',
            padding: 0,
            maxHeight: 132,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          {events.map((e) => {
            const done = e.status === 'DONE';
            const failed = e.status === 'FAILED';
            return (
              <li
                key={e.progress_id}
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: 'baseline',
                  fontSize: 12,
                  opacity: e.progress_id === latest.progress_id ? 1 : 0.62,
                }}
              >
                <span className="mono muted" style={{ flex: '0 0 auto' }}>
                  {hhmmss(e.created_at)}
                </span>
                <span aria-hidden style={{ flex: '0 0 auto' }}>
                  {failed ? '⚠' : done ? '✓' : '·'}
                </span>
                <span className="cjk">{e.message}</span>
              </li>
            );
          })}
        </ul>
      )}

      <style>{`@keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(224,33,138,0.6); }
        100% { box-shadow: 0 0 0 7px rgba(224,33,138,0); }
      }`}</style>
    </div>
  );
}
