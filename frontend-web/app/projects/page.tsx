'use client';

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import {
  createUploadSession,
  getHighlights,
  getProject,
  getTimeline,
  uploadToS3,
} from '@/lib/api';
import { formatMs, msToSecondsLabel, projectPhase } from '@/lib/format';
import { EDITABLE_STATES } from '@/types';
import type { AspectRatio, Highlight, Project, Timeline } from '@/types';
import StatusPill from '@/components/StatusPill';
import StageRail from '@/components/StageRail';
import ScoreMeter from '@/components/ScoreMeter';
import HighlightWave from '@/components/HighlightWave';

const POLL_INTERVAL_MS = 2000;

const ASPECTS: AspectRatio[] = ['16:9', '9:16', '1:1'];
const ASPECT_CSS: Record<AspectRatio, string> = {
  '16:9': '16 / 9',
  '9:16': '9 / 16',
  '1:1': '1 / 1',
};

function isPollable(status: Project['status']): boolean {
  return status === 'UPLOADING' || status === 'ANALYZING' || status === 'COMPOSING';
}

// --- Upload region -------------------------------------------------------
function UploadRegion({
  projectId,
  onUploaded,
}: {
  projectId: string;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function handleUpload() {
    if (!file) {
      setError('請先選擇一個影片檔案。');
      return;
    }
    setError(null);
    setUploading(true);
    setPct(0);
    try {
      const session = await createUploadSession(projectId, {
        filename: file.name,
        content_type: file.type || 'video/mp4',
        size_bytes: file.size,
        part_count: 1,
      });
      await uploadToS3(session, file, setPct);
      onUploaded();
    } catch (err) {
      console.error(err);
      setError('上傳失敗，請重試。');
      setUploading(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel__head">
        <span className="panel__title cjk">上傳原始影片</span>
        <span className="panel__eyebrow">UPLOAD</span>
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        瀏覽器將以 presigned URL 直接上傳至 S3 Raw bucket，完成後自動觸發高光分析。
      </p>

      <label className="dropzone" htmlFor="video" style={{ marginTop: 14 }}>
        <input
          id="video"
          type="file"
          accept="video/*"
          disabled={uploading}
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setError(null);
          }}
        />
        {file ? (
          <span>
            <span className="mono" style={{ color: 'var(--text)' }}>
              {file.name}
            </span>
            {file.size > 0 && (
              <span className="mono muted"> · {(file.size / (1024 * 1024)).toFixed(1)} MB</span>
            )}
          </span>
        ) : (
          <span>拖曳或點選影片檔案（mp4 等）</span>
        )}
      </label>

      {uploading && (
        <>
          <div className="bar" aria-label="upload progress">
            <span style={{ width: `${pct}%` }} />
          </div>
          <p className="hint mono">上傳中… {pct}%</p>
        </>
      )}

      <div style={{ marginTop: 16 }}>
        <button className="btn" onClick={handleUpload} disabled={uploading || !file}>
          {uploading ? '上傳中…' : '開始上傳 ▸'}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// --- Four-region editor --------------------------------------------------
function EditorRegions({
  project,
  highlights,
  timeline,
}: {
  project: Project;
  highlights: Highlight[];
  timeline: Timeline;
}) {
  const [aspect, setAspect] = useState<AspectRatio>(timeline.aspect_ratio ?? '9:16');
  const [subtitleOn, setSubtitleOn] = useState<boolean>(
    timeline.subtitle_settings?.enabled ?? true,
  );
  const [effectIntensity, setEffectIntensity] = useState<'low' | 'medium' | 'high'>(
    timeline.effect_settings?.intensity ?? 'medium',
  );
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(highlights.filter((h) => h.selected !== false).map((h) => h.highlight_id)),
  );
  const [locked, setLocked] = useState<Set<string>>(
    () => new Set(highlights.filter((h) => h.locked).map((h) => h.highlight_id)),
  );

  function toggle(set: Set<string>, id: string): Set<string> {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  }

  const clips = [...timeline.clips].sort((a, b) => a.timeline_order - b.timeline_order);
  const total = timeline.actual_duration_ms || 1;

  return (
    <div className="editor">
      {/* Region 1: Video Preview */}
      <section className="panel col-span">
        <div className="panel__head">
          <span className="panel__title">Video Preview</span>
          <div className="seg">
            {ASPECTS.map((a) => (
              <button
                key={a}
                className={`seg__btn${aspect === a ? ' is-active' : ''}`}
                onClick={() => setAspect(a)}
              >
                {a}
              </button>
            ))}
          </div>
        </div>
        <div className="preview">
          <div
            className="preview__frame"
            style={{ aspectRatio: ASPECT_CSS[aspect], height: 'var(--preview-h)' }}
          >
            <span className="preview__note">PREVIEW · {aspect}</span>
          </div>
        </div>
        <div
          style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10 }}
        >
          <input type="range" min={0} max={100} defaultValue={18} disabled style={{ flex: 1 }} />
          <span className="mono muted" style={{ fontSize: 12 }}>
            0:12.480
          </span>
        </div>
        <p className="hint">
          目標 <span className="mono">{msToSecondsLabel(project.target_duration_ms)}</span> ·{' '}
          <span className="mono">{project.target_duration_ms}</span> ms
          {typeof project.source_duration_ms === 'number' && (
            <>
              {' '}· 原始長度 <span className="mono">{formatMs(project.source_duration_ms)}</span>
            </>
          )}
        </p>
      </section>

      {/* Region 2: Highlight Candidates */}
      <section className="panel">
        <div className="panel__head">
          <span className="panel__title cjk">高光候選</span>
          <span className="panel__eyebrow">HIGHLIGHTS · {highlights.length}</span>
        </div>
        {highlights.length === 0 ? (
          <p className="hint">尚無高光候選。</p>
        ) : (
          highlights.map((h) => (
            <div className="hlx" key={h.highlight_id}>
              <label className="hlx__row">
                <input
                  type="checkbox"
                  checked={selected.has(h.highlight_id)}
                  onChange={() => setSelected((s) => toggle(s, h.highlight_id))}
                />
                <span className="hlx__title cjk">{h.suggested_title || h.highlight_id}</span>
                <ScoreMeter score={h.score ?? 0} />
              </label>
              <div className="hlx__meta mono">
                {h.highlight_id} · {formatMs(h.start_ms)}–{formatMs(h.end_ms)}（{h.start_ms}–
                {h.end_ms} ms）
              </div>
              {h.reason && <p className="hlx__reason">{h.reason}</p>}
              <div className="hlx__foot">
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() => setLocked((s) => toggle(s, h.highlight_id))}
                >
                  {locked.has(h.highlight_id) ? '🔒 已鎖定' : '🔓 鎖定'}
                </button>
                <span className="mono muted" style={{ fontSize: 12 }}>
                  {selected.has(h.highlight_id) ? '已選入' : '未選入'}
                </span>
              </div>
            </div>
          ))
        )}
      </section>

      {/* Region 3: Project Settings */}
      <section className="panel">
        <div className="panel__head">
          <span className="panel__title cjk">專案設定</span>
          <span className="panel__eyebrow">SETTINGS</span>
        </div>

        <div className="setting">
          <span className="setting__label">Target</span>
          <span className="mono">
            {msToSecondsLabel(project.target_duration_ms)} · {project.target_duration_ms} ms
          </span>
        </div>

        <div className="setting">
          <span className="setting__label">Subtitle</span>
          <button
            className={`switch${subtitleOn ? ' is-on' : ''}`}
            role="switch"
            aria-checked={subtitleOn}
            aria-label="字幕自動生成"
            onClick={() => setSubtitleOn((v) => !v)}
          />
        </div>

        <div className="setting">
          <span className="setting__label">Effect</span>
          <div className="seg">
            {(['low', 'medium', 'high'] as const).map((lv) => (
              <button
                key={lv}
                className={`seg__btn${effectIntensity === lv ? ' is-active' : ''}`}
                onClick={() => setEffectIntensity(lv)}
              >
                {lv}
              </button>
            ))}
          </div>
        </div>

        <div className="setting">
          <span className="setting__label">Aspect</span>
          <span className="mono">{aspect}</span>
        </div>
      </section>

      {/* Region 4: Timeline */}
      <section className="panel col-span">
        <div className="panel__head">
          <span className="panel__title">Timeline</span>
          <span className="panel__eyebrow mono">
            v{timeline.version} · {formatMs(timeline.actual_duration_ms)} /{' '}
            {formatMs(timeline.target_duration_ms)}
          </span>
        </div>
        <div className="track">
          <div className="track__ruler">
            <span>0:00.000</span>
            <span>{formatMs(Math.round(timeline.target_duration_ms / 2))}</span>
            <span>{formatMs(timeline.target_duration_ms)}</span>
          </div>
          <div className="track__lane">
            {clips.map((c) => (
              <div
                key={c.timeline_order}
                className="track__clip"
                style={{
                  flexBasis: `${((c.timeline_end_ms - c.timeline_start_ms) / total) * 100}%`,
                }}
                title={`${c.highlight_id} · ${c.timeline_start_ms}–${c.timeline_end_ms} ms`}
              >
                <span className="track__order">#{c.timeline_order}</span>
                <span className="track__id">{c.highlight_id}</span>
                <span className="track__time">
                  {formatMs(c.timeline_start_ms)}–{formatMs(c.timeline_end_ms)}
                </span>
              </div>
            ))}
          </div>
          <span className="playhead" style={{ left: '38%' }} />
        </div>
        <p className="hint">拖曳排序／刪除／鎖定與重新組片為 M2／M3 功能，此處先呈現骨架。</p>
      </section>

      {/* Actions */}
      <div className="panel col-span">
        <div className="actions">
          <button className="btn btn--ghost" disabled title="M3：儲存為新 Timeline 版本">
            Save Draft
          </button>
          <button className="btn" disabled title="M4：提交 Render">
            Render Video ▸
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Page shell ----------------------------------------------------------
function ProjectView() {
  const params = useSearchParams();
  const projectId = params.get('id') ?? '';

  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ highlights: Highlight[]; timeline: Timeline } | null>(
    null,
  );

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    getProject(projectId)
      .then((p) => active && setProject(p))
      .catch((err) => {
        console.error(err);
        if (active) setError('查詢專案狀態失敗，請稍後重試。');
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !project || !isPollable(project.status)) return;
    const t = setTimeout(async () => {
      try {
        setProject(await getProject(projectId));
      } catch (err) {
        console.error(err);
      }
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(t);
  }, [projectId, project]);

  useEffect(() => {
    if (!projectId || !project || !EDITABLE_STATES.has(project.status) || editor) return;
    let active = true;
    Promise.all([getHighlights(projectId), getTimeline(projectId)])
      .then(([hl, tl]) => {
        if (active) setEditor({ highlights: hl.highlights, timeline: tl });
      })
      .catch((err) => console.error(err));
    return () => {
      active = false;
    };
  }, [projectId, project, editor]);

  if (!projectId) {
    return (
      <main className="shell page">
        <div className="panel">
          <h1 className="cjk" style={{ fontSize: 24 }}>
            找不到專案 ID
          </h1>
          <p className="hint">
            網址缺少 <span className="mono">?id=</span> 參數。
          </p>
          <div style={{ marginTop: 16 }}>
            <Link href="/" className="btn btn--ghost btn--sm">
              ← 建立新專案
            </Link>
          </div>
        </div>
      </main>
    );
  }

  const phase = project ? projectPhase(project.status) : null;

  return (
    <main className="shell page">
      {/* Project header */}
      <div className="panel">
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <div>
            <div className="mono muted" style={{ fontSize: 11, letterSpacing: '0.12em' }}>
              PROJECT · {projectId}
            </div>
            <h1 className="cjk" style={{ fontSize: 26, marginTop: 6 }}>
              {project?.title || '影片專案'}
            </h1>
            {phase && <p className="hint cjk" style={{ marginTop: 4 }}>{phase.label}</p>}
          </div>
          {project && <StatusPill status={project.status} />}
        </div>
        {project && <StageRail status={project.status} />}
        {!project && !error && <p className="hint">載入中…</p>}
        {error && <p className="error">{error}</p>}
        {project?.status === 'FAILED' && (
          <p className="error">
            分析失敗，請重新上傳影片。{project.error_code} {project.error_message}
          </p>
        )}
      </div>

      {project && phase?.awaitingUpload && (
        <div style={{ marginTop: 20 }}>
          <UploadRegion
            projectId={projectId}
            onUploaded={() => setProject((p) => (p ? { ...p, status: 'UPLOADING' } : p))}
          />
        </div>
      )}

      {project && phase?.busy && !phase.canEdit && (
        <div className="panel" style={{ marginTop: 20 }}>
          <div className="panel__head">
            <span className="panel__title cjk">{phase.label}</span>
            <span className="panel__eyebrow">PROCESSING</span>
          </div>
          <HighlightWave mode="scan" height={120} />
          <p className="hint mono">處理中 · 每 {POLL_INTERVAL_MS / 1000}s 自動更新狀態…</p>
        </div>
      )}

      {project &&
        phase?.canEdit &&
        (editor ? (
          <EditorRegions
            project={project}
            highlights={editor.highlights}
            timeline={editor.timeline}
          />
        ) : (
          <div className="panel" style={{ marginTop: 20 }}>
            <p className="hint">載入編輯器資料中…</p>
          </div>
        ))}

      <div style={{ marginTop: 24 }}>
        <Link href="/" className="mono muted" style={{ fontSize: 13 }}>
          ← 建立另一個專案
        </Link>
      </div>
    </main>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense
      fallback={
        <main className="shell page">
          <div className="panel">
            <p className="hint">載入中…</p>
          </div>
        </main>
      }
    >
      <ProjectView />
    </Suspense>
  );
}
