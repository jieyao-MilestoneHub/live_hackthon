'use client';

import type { Artifact, EditPlan, EffectType, Route } from '@/types';
import { msToSecondsLabel } from '@/lib/format';

/** 雙軌路線標籤：pipeline=模板版、agent=指令版。 */
export const ROUTE_LABEL: Record<Route, string> = { pipeline: '模板版', agent: '指令版' };
const ROUTE_SUB: Record<Route, string> = { pipeline: '規則式 · 穩定', agent: 'AI 依指令客製' };

const EFFECT_LABEL: Record<EffectType, string> = {
  zoom_in: '放大',
  zoom_out: '縮小',
  pan: '平移',
  shake: '震動',
  flash_transition: '閃切',
  cut: '硬切',
};

const MB = 1024 * 1024;

/** Count effects by type → ["放大×2", "閃切×1", …] so users see what the track did. */
function effectSummary(plan?: EditPlan | null): string[] {
  const list = plan?.effects?.effects ?? [];
  const counts = new Map<EffectType, number>();
  for (const e of list) counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
  return [...counts.entries()].map(([type, n]) => `${EFFECT_LABEL[type] ?? type}×${n}`);
}

/** Unique 爆點字 across keyword cues (the animated punch words). */
function emphasisWords(plan?: EditPlan | null): string[] {
  const cues = plan?.subtitle?.cues ?? [];
  const seen = new Set<string>();
  for (const c of cues) {
    if (c.kind === 'keyword') for (const w of c.emphasis_words ?? []) seen.add(w);
  }
  return [...seen].slice(0, 8);
}

/**
 * One creative track's result card. Shows WHAT the version did (effects + 爆點字
 * + output spec) so the two versions are never a black box. While the render is
 * still running it shows the live stage line instead.
 */
export default function TrackSummaryCard({
  route,
  instruction,
  artifact,
  plan,
  stageLine,
  publishGated,
  downloading,
  onDownload,
}: {
  route: Route;
  instruction?: string;
  artifact?: Artifact;
  plan?: EditPlan | null;
  stageLine?: string;
  publishGated?: boolean;
  downloading?: string | null;
  onDownload?: (artifactId: string) => void;
}) {
  const ready = artifact?.status === 'READY';
  const effects = effectSummary(plan);
  const words = emphasisWords(plan);

  return (
    <div className={`tracksum tracksum--${route}`}>
      <div className="tracksum__head">
        <span className={`tag ${route === 'agent' ? 'tag--crest' : 'tag--tide'}`}>{ROUTE_LABEL[route]}</span>
        <span className="tracksum__sub muted">{ROUTE_SUB[route]}</span>
      </div>

      {route === 'agent' && (
        <p className="tracksum__intent cjk">
          {instruction ? (
            <>指令：<span className="tracksum__intent-q">「{instruction}」</span></>
          ) : (
            <span className="muted">未下指令 → 套用預設模板</span>
          )}
        </p>
      )}

      {ready ? (
        <>
          <div className="tracksum__frame" aria-hidden>
            <span className="mono">{artifact?.aspect_ratio ?? '9:16'}</span>
          </div>

          {effects.length > 0 && (
            <div className="tracksum__block">
              <span className="tracksum__k mono">特效</span>
              <div className="tracksum__chips">
                {effects.map((e) => (
                  <span key={e} className="chip chip--static">{e}</span>
                ))}
              </div>
            </div>
          )}

          {words.length > 0 && (
            <div className="tracksum__block">
              <span className="tracksum__k mono">爆點字</span>
              <div className="tracksum__chips">
                {words.map((w) => (
                  <span key={w} className="chip chip--kw cjk">{w}</span>
                ))}
              </div>
            </div>
          )}

          <div className="tracksum__spec mono muted">
            {artifact?.duration_ms != null && <span>{msToSecondsLabel(artifact.duration_ms)}</span>}
            {artifact?.resolution && <span>{artifact.resolution.width}×{artifact.resolution.height}</span>}
            {artifact?.size_bytes != null && <span>{Math.round(artifact.size_bytes / MB)} MB</span>}
          </div>

          {artifact && onDownload && (
            <button
              type="button"
              className="btn btn--block"
              style={{ marginTop: 12 }}
              disabled={downloading === artifact.artifact_id || publishGated}
              title={publishGated ? '內容審核未通過，無法下載' : `下載${ROUTE_LABEL[route]}`}
              onClick={() => onDownload(artifact.artifact_id)}
            >
              {downloading === artifact.artifact_id ? '取得連結…' : `下載${ROUTE_LABEL[route]} ⬇`}
            </button>
          )}
        </>
      ) : (
        <div className="tracksum__pending">
          <span className="feed__dot is-live" aria-hidden />
          <span className="cjk">{stageLine ?? '準備中…'}</span>
        </div>
      )}
    </div>
  );
}
