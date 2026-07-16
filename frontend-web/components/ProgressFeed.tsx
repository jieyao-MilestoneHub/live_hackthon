'use client';

import { useEffect, useRef, useState } from 'react';
import { getProgress } from '@/lib/api';
import type { ProgressView } from '@/types';

const POLL_MS = 2000;

function hhmmss(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('zh-TW', { hour12: false });
}

/**
 * Live, AI-synthesized progress feed shown while the pipeline is working — so
 * production is never a black box. Polls GET /projects/{id}/progress every 2s
 * while `active`; renders the latest narration prominently plus a chronological
 * ✓/·/⚠ log. Renders nothing until the first event arrives (empty on a backend
 * without /progress). Pass `demo` to mark the feed as synthesized (offline).
 */
export default function ProgressFeed({
  projectId,
  active,
  demo,
}: {
  projectId: string;
  active: boolean;
  demo?: boolean;
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
    if (!active) {
      return () => {
        alive = false;
      };
    }
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
    <div className="feed">
      <div className="feed__eyebrow mono">AI 進度旁白{demo ? ' · 示範' : ''}</div>
      <div className="feed__latest">
        <span className={`feed__dot${running ? ' is-live' : ''}`} aria-hidden />
        <strong className="cjk feed__msg">{latest.message}</strong>
      </div>

      {events.length > 1 && (
        <ul ref={logRef} className="feed__log">
          {events.map((e) => {
            const done = e.status === 'DONE';
            const failed = e.status === 'FAILED';
            return (
              <li
                key={e.progress_id}
                className={`feed__item${e.progress_id === latest.progress_id ? ' is-latest' : ''}`}
              >
                <span className="mono muted feed__time">{hhmmss(e.created_at)}</span>
                <span className={`feed__mark${failed ? ' is-bad' : done ? ' is-done' : ''}`} aria-hidden>
                  {failed ? '⚠' : done ? '✓' : '·'}
                </span>
                <span className="cjk feed__line">{e.message}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
