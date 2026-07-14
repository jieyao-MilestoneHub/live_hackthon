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
import { badgeClass, formatMs, msToSecondsLabel, projectPhase } from '@/lib/format';
import { EDITABLE_STATES } from '@/types';
import type { AspectRatio, Highlight, Project, Timeline } from '@/types';

const POLL_INTERVAL_MS = 2000;
const PREVIEW_HEIGHT = 300;

const ASPECTS: AspectRatio[] = ['16:9', '9:16', '1:1'];
const ASPECT_CSS: Record<AspectRatio, string> = {
  '16:9': '16 / 9',
  '9:16': '9 / 16',
  '1:1': '1 / 1',
};

/** Server-side processing states that warrant polling (excludes await-upload). */
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
    <div className="card">
      <h2>上傳原始影片</h2>
      <p className="hint" style={{ marginTop: 0 }}>
        瀏覽器將以 presigned URL 直接上傳至 S3 Raw bucket，完成後自動觸發高光分析。
      </p>
      <label htmlFor="video">影片檔案（mp4 等）</label>
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
      {file && (
        <p className="hint">
          已選擇：<span className="mono">{file.name}</span>
          {file.size > 0 && <> · {(file.size / (1024 * 1024)).toFixed(1)} MB</>}
        </p>
      )}

      {uploading && (
        <>
          <div className="progress" aria-label="upload progress">
            <span style={{ width: `${pct}%` }} />
          </div>
          <p className="hint">上傳中… {pct}%</p>
        </>
      )}

      <div className="row spacer">
        <button onClick={handleUpload} disabled={uploading || !file}>
          {uploading ? '上傳中…' : '開始上傳'}
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
      <section className="card region-preview">
        <div className="region-head">
          <h2>Video Preview</h2>
          <span className="mono muted">{aspect}</span>
        </div>
        <div className="preview-wrap">
          <div
            className="preview-frame"
            style={{ aspectRatio: ASPECT_CSS[aspect], height: PREVIEW_HEIGHT }}
          >
            <span className="preview-note">預覽（骨架）· {aspect}</span>
          </div>
        </div>
        <p className="hint">
          目標長度 <span className="mono">{msToSecondsLabel(project.target_duration_ms)}</span>
          （<span className="mono">{project.target_duration_ms}</span> ms）
          {typeof project.source_duration_ms === 'number' && (
            <>
              {' '}· 原始長度 <span className="mono">{formatMs(project.source_duration_ms)}</span>
            </>
          )}
        </p>
      </section>

      {/* Region 2: Highlight Candidates */}
      <section className="card region-highlights">
        <h2>Highlight Candidates（{highlights.length}）</h2>
        {highlights.length === 0 ? (
          <p className="hint">尚無高光候選。</p>
        ) : (
          highlights.map((h) => (
            <div className="hl" key={h.highlight_id}>
              <label className="hl-row">
                <input
                  type="checkbox"
                  checked={selected.has(h.highlight_id)}
                  onChange={() => setSelected((s) => toggle(s, h.highlight_id))}
                />
                <span className="hl-title">{h.suggested_title || h.highlight_id}</span>
                <span className="clip-score">{Math.round((h.score ?? 0) * 100)}</span>
              </label>
              <div className="clip-meta">
                <span className="mono">{h.highlight_id}</span> ·{' '}
                <span className="mono">
                  {formatMs(h.start_ms)}–{formatMs(h.end_ms)}
                </span>{' '}
                （<span className="mono">{h.start_ms}</span>–
                <span className="mono">{h.end_ms}</span> ms）
              </div>
              {h.reason && <p className="clip-reason">{h.reason}</p>}
              <button
                className="secondary sm"
                onClick={() => setLocked((s) => toggle(s, h.highlight_id))}
              >
                {locked.has(h.highlight_id) ? '🔒 已鎖定' : '🔓 鎖定'}
              </button>
            </div>
          ))
        )}
      </section>

      {/* Region 3: Project Settings */}
      <section className="card region-settings">
        <h2>Project Settings</h2>

        <div className="setting">
          <span className="setting-label">Target</span>
          <span className="mono">
            {msToSecondsLabel(project.target_duration_ms)} · {project.target_duration_ms} ms
          </span>
        </div>

        <div className="setting">
          <span className="setting-label">Subtitle</span>
          <button
            className={`toggle ${subtitleOn ? 'on' : ''}`}
            onClick={() => setSubtitleOn((v) => !v)}
          >
            {subtitleOn ? 'Auto（開）' : '關閉'}
          </button>
        </div>

        <div className="setting">
          <span className="setting-label">Effect</span>
          <div className="seg">
            {(['low', 'medium', 'high'] as const).map((lv) => (
              <button
                key={lv}
                className={`seg-btn ${effectIntensity === lv ? 'active' : ''}`}
                onClick={() => setEffectIntensity(lv)}
              >
                {lv}
              </button>
            ))}
          </div>
        </div>

        <div className="setting">
          <span className="setting-label">Aspect</span>
          <div className="seg">
            {ASPECTS.map((a) => (
              <button
                key={a}
                className={`seg-btn ${aspect === a ? 'active' : ''}`}
                onClick={() => setAspect(a)}
              >
                {a}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Region 4: Timeline */}
      <section className="card region-timeline">
        <div className="region-head">
          <h2>Timeline</h2>
          <span className="mono muted">
            v{timeline.version} · {formatMs(timeline.actual_duration_ms)} /{' '}
            {formatMs(timeline.target_duration_ms)}
          </span>
        </div>
        <div className="track">
          {clips.map((c) => (
            <div
              key={c.timeline_order}
              className="track-clip"
              style={{ flexBasis: `${((c.timeline_end_ms - c.timeline_start_ms) / total) * 100}%` }}
              title={`${c.highlight_id} · ${c.timeline_start_ms}–${c.timeline_end_ms} ms`}
            >
              <span className="track-order">#{c.timeline_order}</span>
              <span className="track-id mono">{c.highlight_id}</span>
              <span className="track-time mono">
                {formatMs(c.timeline_start_ms)}–{formatMs(c.timeline_end_ms)}
              </span>
            </div>
          ))}
        </div>
        <p className="hint">
          拖曳排序／刪除／鎖定與重新組片為 M2／M3 功能，此處先呈現骨架排版。
        </p>
      </section>

      {/* Action bar */}
      <div className="card region-actions">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <button className="secondary" disabled title="M3：儲存為新 Timeline 版本">
            Save Draft
          </button>
          <button disabled title="M4：提交 Render">
            Render Video
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

  // Initial + manual refresh fetch.
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

  // Poll while the backend is processing.
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

  // Load highlights + timeline once the project is editable.
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
      <div className="card">
        <h1>找不到專案 ID</h1>
        <p className="hint">
          網址缺少 <span className="mono">?id=</span> 參數。
        </p>
        <div className="spacer">
          <Link href="/">← 建立新專案</Link>
        </div>
      </div>
    );
  }

  const phase = project ? projectPhase(project.status) : null;

  return (
    <main>
      <div className="card">
        <div className="region-head">
          <h1 style={{ margin: 0 }}>{project?.title || '影片專案'}</h1>
          {project && <span className={badgeClass(project.status)}>{project.status}</span>}
        </div>
        <p className="clip-meta">
          Project ID：<span className="mono">{projectId}</span>
        </p>
        {!project && !error && <p className="hint">載入中…</p>}
        {error && <p className="error">{error}</p>}
        {project && phase && (
          <p className="hint" style={{ marginTop: 4 }}>
            狀態：{phase.label}
          </p>
        )}
        {project?.status === 'FAILED' && (
          <p className="error">
            失敗：{project.error_code} {project.error_message}
          </p>
        )}
      </div>

      {project && phase?.awaitingUpload && (
        <UploadRegion
          projectId={projectId}
          onUploaded={() =>
            setProject((p) => (p ? { ...p, status: 'UPLOADING' } : p))
          }
        />
      )}

      {project && phase?.busy && !phase.canEdit && (
        <div className="card">
          <h2>{phase.label}</h2>
          <div className="progress indeterminate" aria-label="processing">
            <span />
          </div>
          <p className="hint">處理中，每 {POLL_INTERVAL_MS / 1000} 秒自動更新狀態…</p>
        </div>
      )}

      {project && phase?.canEdit && (
        editor ? (
          <EditorRegions
            project={project}
            highlights={editor.highlights}
            timeline={editor.timeline}
          />
        ) : (
          <div className="card">
            <p className="hint">載入編輯器資料中…</p>
          </div>
        )
      )}

      <div className="spacer">
        <Link href="/">← 建立另一個專案</Link>
      </div>
    </main>
  );
}

export default function ProjectsPage() {
  // useSearchParams requires a Suspense boundary under static export.
  return (
    <Suspense
      fallback={
        <div className="card">
          <p className="hint">載入中…</p>
        </div>
      }
    >
      <ProjectView />
    </Suspense>
  );
}
