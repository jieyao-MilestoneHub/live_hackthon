'use client';

import { useEffect, useState } from 'react';
import { getModeration, overrideModeration } from '@/lib/api';
import { moderationDisplay } from '@/lib/format';
import { isModerator } from '@/lib/auth';
import type { ModerationStatus, ModerationView, Project } from '@/types';

// Only surface a banner for decided/actionable verdicts (a clean ALLOWED or an
// unscanned PENDING-during-analysis project shows nothing).
const SHOW_FOR: ModerationStatus[] = ['FLAGGED', 'BLOCKED', 'OVERRIDDEN'];

/** Content-moderation banner: verdict + findings + (moderators only) override. */
export default function ModerationBanner({
  project,
  onOverridden,
}: {
  project: Project;
  onOverridden: (status: ModerationStatus) => void;
}) {
  const status = project.moderation_status;
  const [view, setView] = useState<ModerationView | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canModerate = isModerator();

  useEffect(() => {
    if (!status || !SHOW_FOR.includes(status)) return;
    let alive = true;
    getModeration(project.project_id)
      .then((v) => alive && setView(v))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [project.project_id, status]);

  if (!status || !SHOW_FOR.includes(status)) return null;
  const disp = moderationDisplay(status);

  async function act(decision: 'ALLOW' | 'BLOCK') {
    setBusy(true);
    setErr(null);
    try {
      const v = await overrideModeration(project.project_id, decision);
      setView(v);
      onOverridden(v.status);
    } catch {
      setErr('複核失敗（需 moderator 角色，且需登入）。');
    } finally {
      setBusy(false);
    }
  }

  const latest = view?.latest;
  const visualLabels: any[] = (latest?.visual as any)?.labels ?? [];
  const textFindings: any[] = (latest?.text as any)?.findings ?? [];

  return (
    <div className={`mod-banner mod-banner--${disp.tone}`}>
      <div className="mod-banner__head">
        <strong className="cjk">內容審核：{disp.label}</strong>
        {latest?.policy_version && <span className="mono muted">{latest.policy_version}</span>}
      </div>

      {(visualLabels.length > 0 || textFindings.length > 0) && (
        <ul className="mod-banner__findings">
          {visualLabels.slice(0, 5).map((l, i) => (
            <li key={`v${i}`}>
              視覺 · {l.name}（{Math.round(l.confidence)}%）
            </li>
          ))}
          {textFindings.slice(0, 5).map((f, i) => (
            <li key={`t${i}`}>
              文字/{f.source} · {f.category}（{Math.round((f.severity || 0) * 100)}%）
              {f.quote ? ` — ${f.quote}` : ''}
            </li>
          ))}
        </ul>
      )}

      {status !== 'OVERRIDDEN' &&
        (canModerate ? (
          <div className="mod-banner__actions">
            <button className="btn btn--sm" disabled={busy} onClick={() => act('ALLOW')}>
              {busy ? '處理中…' : '放行（override）'}
            </button>
            <button
              className="btn btn--sm btn--ghost"
              disabled={busy}
              onClick={() => act('BLOCK')}
            >
              維持封鎖
            </button>
          </div>
        ) : (
          <p className="hint">此專案需管理員複核後才能發布（渲染／下載已鎖定）。</p>
        ))}

      {latest?.decided_by && (
        <p className="mono muted" style={{ fontSize: 11, marginTop: 6 }}>
          最新裁決：{latest.action} · {latest.decided_by} · {latest.decided_at}
        </p>
      )}
      {err && <p className="error">{err}</p>}
    </div>
  );
}
