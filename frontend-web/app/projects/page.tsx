'use client';

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import {
  composeTimeline,
  createChatUpload,
  createRender,
  createUploadSession,
  getDownloadUrl,
  getHighlights,
  getProject,
  getRender,
  getTimeline,
  listArtifacts,
  setVideoTimebase,
  updateTimeline,
  uploadChatCsv,
  uploadToS3,
} from '@/lib/api';
import { readVideoDurationMs } from '@/lib/media';
import {
  formatMs,
  moderationAllowsPublish,
  msToSecondsLabel,
  projectPhase,
} from '@/lib/format';
import {
  EDITABLE_STATES,
  POLLABLE_PROJECT_STATES,
  RENDER_TERMINAL_STATES,
} from '@/types';
import type {
  Artifact,
  AspectRatio,
  ComposeRequest,
  Highlight,
  ModerationStatus,
  Project,
  Render,
  RenderCreated,
  Route,
  Timeline,
  TimelineClip,
} from '@/types';
import StatusPill from '@/components/StatusPill';
import StageRail from '@/components/StageRail';
import ModerationBanner from '@/components/ModerationBanner';
import ScoreMeter from '@/components/ScoreMeter';
import HighlightWave from '@/components/HighlightWave';

const POLL_INTERVAL_MS = 2000;

/** 雙軌分流：下載鍵的路線標籤。 */
const ROUTE_LABEL: Record<Route, string> = { pipeline: 'Pipeline 版', agent: 'AI Agent 版' };

const ASPECTS: AspectRatio[] = ['16:9', '9:16', '1:1'];
const ASPECT_CSS: Record<AspectRatio, string> = {
  '16:9': '16 / 9',
  '9:16': '9 / 16',
  '1:1': '1 / 1',
};

function isPollable(status: Project['status']): boolean {
  return POLLABLE_PROJECT_STATES.has(status);
}

const sortedClips = (clips: TimelineClip[]): TimelineClip[] =>
  [...clips].sort((a, b) => a.timeline_order - b.timeline_order);

/**
 * Rebuild a Timeline request body from the current (possibly reordered / trimmed)
 * clip list — renumbers timeline_order and re-packs the timeline offsets so the
 * clips are contiguous from 0. The server assigns the new version number.
 */
function buildTimelineBody(
  base: Timeline,
  project: Project,
  clips: TimelineClip[],
  opts: { aspect: AspectRatio; subtitleOn: boolean; effectIntensity: 'low' | 'medium' | 'high' },
): { body: Timeline; actualMs: number } {
  let cursor = 0;
  const packed: TimelineClip[] = clips.map((c, i) => {
    const dur = Math.max(0, c.source_end_ms - c.source_start_ms);
    const timeline_start_ms = cursor;
    const timeline_end_ms = cursor + dur;
    cursor = timeline_end_ms;
    return {
      timeline_order: i + 1,
      highlight_id: c.highlight_id,
      source_start_ms: c.source_start_ms,
      source_end_ms: c.source_end_ms,
      timeline_start_ms,
      timeline_end_ms,
    };
  });
  const body: Timeline = {
    schema_version: base.schema_version ?? 'timeline.v1',
    project_id: project.project_id,
    version: base.version,
    target_duration_ms: project.target_duration_ms ?? base.target_duration_ms,
    actual_duration_ms: cursor,
    aspect_ratio: opts.aspect,
    subtitle_settings: { enabled: opts.subtitleOn, mode: base.subtitle_settings?.mode ?? 'auto' },
    effect_settings: { enabled: true, intensity: opts.effectIntensity },
    clips: packed,
  };
  return { body, actualMs: cursor };
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
  const [logFile, setLogFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [pct, setPct] = useState(0);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleUpload() {
    if (!file) {
      setError('請先選擇一個影片檔案。');
      return;
    }
    if (!logFile) {
      setError('需同時選擇一個聊天室 LOG CSV 檔案。');
      return;
    }
    setError(null);
    setUploading(true);
    setPct(0);
    try {
      setStep('上傳影片…');
      const session = await createUploadSession(projectId, {
        filename: file.name,
        content_type: file.type || 'video/mp4',
        size_bytes: file.size,
        // No part_count: the server derives it from size_bytes (real multipart).
      });
      // uploadToS3 PUTs each part, collects ETags, then POSTs the multipart
      // -complete handshake — which materializes source.mp4. The Starter skips
      // auto-Transcribe (analysis_source="chat" gate); the chat LOG below drives
      // the pipeline instead.
      await uploadToS3(projectId, session, file, setPct);

      // 1) link the video timebase FIRST, so the auto-analysis sees source_duration_ms.
      const durationMs = await readVideoDurationMs(file);
      if (durationMs) {
        await setVideoTimebase(projectId, { source_duration_ms: durationMs });
      }
      // 2) upload the chat LOG — dropping chat.csv AUTO-triggers the whole
      //    pipeline (analyze → compose → render → artifact) via the chat_starter
      //    Lambda. The UI must NOT also call analyze/compose (they'd 409 / race
      //    the auto-trigger); it just polls the project status to ARTIFACT_READY.
      setStep('上傳聊天室 LOG，啟動自動分析與渲染…');
      const chatSession = await createChatUpload(projectId);
      await uploadChatCsv(chatSession, logFile);

      onUploaded();
    } catch (err) {
      console.error(err);
      setError('上傳或分析失敗，請重試。');
      setUploading(false);
      setStep(null);
    }
  }

  const uploadDisabled = uploading || !file || !logFile;

  return (
    <div className="panel">
      <div className="panel__head">
        <span className="panel__title cjk">上傳影片與聊天室 LOG</span>
        <span className="panel__eyebrow">UPLOAD</span>
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        影片以 presigned URL 直傳 S3；聊天室 LOG CSV 供分析彈幕熱度峰值，完成後自動組出初始剪輯。
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

      <label className="dropzone" htmlFor="chatlog" style={{ marginTop: 12 }}>
        <input
          id="chatlog"
          type="file"
          accept=".csv,text/csv"
          disabled={uploading}
          onChange={(e) => {
            setLogFile(e.target.files?.[0] ?? null);
            setError(null);
          }}
        />
        {logFile ? (
          <span>
            <span className="mono" style={{ color: 'var(--text)' }}>
              {logFile.name}
            </span>
            {logFile.size > 0 && (
              <span className="mono muted"> · {(logFile.size / (1024 * 1024)).toFixed(1)} MB</span>
            )}
          </span>
        ) : (
          <span>拖曳或點選聊天室 LOG（.csv）</span>
        )}
      </label>

      {uploading && (
        <>
          <div className="bar" aria-label="upload progress">
            <span style={{ width: `${pct}%` }} />
          </div>
          <p className="hint mono">
            {step ?? '處理中…'} {pct > 0 && pct < 100 ? `${pct}%` : ''}
          </p>
        </>
      )}

      <div style={{ marginTop: 16 }}>
        <button className="btn" onClick={handleUpload} disabled={uploadDisabled}>
          {uploading ? '處理中…' : '上傳並分析 ▸'}
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
  render,
  onProjectPatch,
  onRenderStarted,
}: {
  project: Project;
  highlights: Highlight[];
  timeline: Timeline;
  render: Render | null;
  onProjectPatch: (patch: Partial<Project>) => void;
  onRenderStarted: (created: RenderCreated) => void;
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

  // Editable timeline state (reorder / delete persist via Save Draft → PUT).
  const [clips, setClips] = useState<TimelineClip[]>(() => sortedClips(timeline.clips));
  const [version, setVersion] = useState<number>(timeline.version);
  const [actualMs, setActualMs] = useState<number>(timeline.actual_duration_ms);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [recomposing, setRecomposing] = useState(false);
  const [renderErr, setRenderErr] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadErr, setDownloadErr] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);

  function toggle(set: Set<string>, id: string): Set<string> {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  }

  function moveClip(index: number, dir: -1 | 1) {
    setClips((prev) => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setSaveMsg(null);
  }

  function deleteClip(index: number) {
    setClips((prev) => prev.filter((_, i) => i !== index));
    setSaveMsg(null);
  }

  async function handleSave() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const { body, actualMs: newActual } = buildTimelineBody(timeline, project, clips, {
        aspect,
        subtitleOn,
        effectIntensity,
      });
      const res = await updateTimeline(project.project_id, body);
      setVersion(res.timeline_version);
      setSavedVersion(res.timeline_version);
      setActualMs(newActual);
      setSaveMsg(`已儲存為 v${res.timeline_version}（${clips.length} 段）`);
      onProjectPatch({ latest_timeline_version: res.timeline_version });
    } catch (err) {
      console.error(err);
      setSaveMsg(null);
      setRenderErr('儲存失敗，請重試。');
    } finally {
      setSaving(false);
    }
  }

  async function handleRecompose() {
    setRecomposing(true);
    setSaveMsg(null);
    try {
      const body: ComposeRequest = {
        target_duration_ms: project.target_duration_ms,
        locked_highlight_ids: [...locked],
        excluded_highlight_ids: highlights
          .filter((h) => !selected.has(h.highlight_id))
          .map((h) => h.highlight_id),
      };
      await composeTimeline(project.project_id, body);
      const tl = await getTimeline(project.project_id);
      setClips(sortedClips(tl.clips));
      setVersion(tl.version);
      setActualMs(tl.actual_duration_ms);
      setSavedVersion(tl.version);
      setSaveMsg(`已重新組片（v${tl.version}）`);
      onProjectPatch({ latest_timeline_version: tl.version });
    } catch (err) {
      console.error(err);
      setRenderErr('重新組片失敗，請重試。');
    } finally {
      setRecomposing(false);
    }
  }

  async function handleRender() {
    setRenderErr(null);
    try {
      const useVersion = savedVersion ?? project.latest_timeline_version ?? version;
      const created = await createRender(project.project_id, useVersion);
      onRenderStarted(created);
    } catch (err) {
      console.error(err);
      setRenderErr('提交渲染失敗，請重試。');
    }
  }

  const fallbackArtifactId = render?.artifact_id || project.latest_artifact_id;
  const renderActive = !!render && !RENDER_TERMINAL_STATES.has(render.status);
  const renderDone =
    render?.status === 'SUCCEEDED' || project.status === 'ARTIFACT_READY';
  // Mirror the backend gate: a set-but-not-publishable verdict locks render/download.
  // (undefined = moderation off / pre-moderation project → not gated.)
  const publishGated =
    !!project.moderation_status && !moderationAllowsPublish(project.moderation_status);

  // 雙軌分流：成品就緒後列出所有 route 的 artifact，各給一顆下載鍵。
  useEffect(() => {
    if (!renderDone) return;
    let active = true;
    listArtifacts(project.project_id)
      .then((a) => {
        if (active) setArtifacts(a);
      })
      .catch((err) => console.error(err));
    return () => {
      active = false;
    };
  }, [renderDone, project.project_id, render?.status]);

  async function handleDownload(artifactId: string) {
    setDownloadErr(null);
    setDownloading(artifactId);
    try {
      const { url } = await getDownloadUrl(artifactId);
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      console.error(err);
      setDownloadErr('取得下載連結失敗，請重試。');
    } finally {
      setDownloading(null);
    }
  }

  const total = actualMs || 1;

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
            v{version} · {formatMs(actualMs)} / {formatMs(timeline.target_duration_ms)}
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
                key={`${c.highlight_id}-${c.timeline_order}`}
                className="track__clip"
                style={{
                  flexBasis: `${((c.source_end_ms - c.source_start_ms) / total) * 100}%`,
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

        {/* Editable clip list: reorder (up/down) + delete, then Save Draft persists */}
        {clips.length === 0 ? (
          <p className="hint">時間軸沒有片段了，重新組片或加回高光候選。</p>
        ) : (
          <div className="cliplist">
            {clips.map((c, i) => (
              <div className="cliprow" key={`${c.highlight_id}-${i}`}>
                <span className="cliprow__order">#{i + 1}</span>
                <span className="cliprow__id">{c.highlight_id}</span>
                <span className="cliprow__time">
                  {formatMs(c.source_start_ms)}–{formatMs(c.source_end_ms)}
                </span>
                <div className="cliprow__ops">
                  <button
                    className="clip__op"
                    onClick={() => moveClip(i, -1)}
                    disabled={i === 0}
                    aria-label="上移"
                    title="上移"
                  >
                    ↑
                  </button>
                  <button
                    className="clip__op"
                    onClick={() => moveClip(i, 1)}
                    disabled={i === clips.length - 1}
                    aria-label="下移"
                    title="下移"
                  >
                    ↓
                  </button>
                  <button
                    className="clip__op"
                    onClick={() => deleteClip(i)}
                    aria-label="刪除"
                    title="刪除"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="hint">調整排序／刪除片段後，按「Save Draft」存成新的 Timeline 版本。</p>
      </section>

      {/* Actions */}
      <div className="panel col-span">
        <div className="actions">
          <button
            className="btn btn--ghost"
            onClick={handleRecompose}
            disabled={recomposing || saving}
            title="以目前的選取／鎖定重新組片"
          >
            {recomposing ? '重新組片中…' : '↻ 重新組片'}
          </button>
          <div style={{ display: 'flex', gap: 12 }}>
            <button
              className="btn btn--ghost"
              onClick={handleSave}
              disabled={saving || recomposing}
              title="儲存為新 Timeline 版本"
            >
              {saving ? '儲存中…' : 'Save Draft'}
            </button>
            {renderDone && artifacts.length > 0 ? (
              // 雙軌分流：每個 route 一顆下載鍵；publishGated（內容審核未放行）時鎖定。
              [...artifacts]
                .sort((a, b) => (a.route ?? '').localeCompare(b.route ?? ''))
                .map((a) => (
                  <button
                    key={a.artifact_id}
                    className="btn"
                    onClick={() => handleDownload(a.artifact_id)}
                    disabled={downloading === a.artifact_id || publishGated}
                    title={publishGated ? '內容審核未通過，無法下載' : `下載 ${a.route ?? 'pipeline'} 版成品`}
                  >
                    {downloading === a.artifact_id
                      ? '取得連結…'
                      : `下載成品（${ROUTE_LABEL[a.route ?? 'pipeline']}）⬇`}
                  </button>
                ))
            ) : renderDone && fallbackArtifactId ? (
              <button
                className="btn"
                onClick={() => fallbackArtifactId && handleDownload(fallbackArtifactId)}
                disabled={!!downloading || publishGated}
              >
                {downloading ? '取得連結…' : '下載成品 ⬇'}
              </button>
            ) : (
              <button
                className="btn"
                onClick={handleRender}
                disabled={renderActive || saving || recomposing || clips.length === 0 || publishGated}
                title={publishGated ? '內容審核未通過，無法渲染' : '凍結目前版本並提交渲染'}
              >
                {renderActive ? '渲染中…' : 'Render Video ▸'}
              </button>
            )}
          </div>
        </div>
        {publishGated && (
          <p className="hint">內容審核未通過，渲染／下載已鎖定，需管理員複核放行。</p>
        )}

        {saveMsg && <p className="note-ok">{saveMsg}</p>}
        {render && (
          <div className="render-status">
            <span className={`pill pill--${renderDone ? 'done' : 'live'}`}>
              <span className="pill__dot" />
              {render.status}
            </span>
            {render.current_stage && <span>· {render.current_stage}</span>}
            {render.timeline_version != null && (
              <span className="muted">· timeline v{render.timeline_version}</span>
            )}
          </div>
        )}
        {render?.status === 'FAILED' && (
          <p className="error">渲染失敗。{render.error_code} {render.error_message}</p>
        )}
        {renderErr && <p className="error">{renderErr}</p>}
        {downloadErr && <p className="error">{downloadErr}</p>}
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
  const [render, setRender] = useState<Render | null>(null);

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

  // Poll project status while backend work is in flight (upload → analyze →
  // compose, and render_requested → rendering).
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

  // Load the editor data once the project is editable.
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

  // Poll the active render for stage/status; on success refresh the project so
  // it flips to ARTIFACT_READY and carries latest_artifact_id.
  useEffect(() => {
    if (!render || RENDER_TERMINAL_STATES.has(render.status)) return;
    let active = true;
    const t = setTimeout(async () => {
      try {
        const r = await getRender(render.render_id);
        if (!active) return;
        setRender(r);
        if (r.status === 'SUCCEEDED') {
          try {
            setProject(await getProject(projectId));
          } catch {
            /* keep the render result even if the project refresh fails */
          }
        }
      } catch (err) {
        console.error(err);
      }
    }, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [render, projectId]);

  function handleRenderStarted(created: RenderCreated) {
    setRender({ render_id: created.render_id, project_id: projectId, status: created.status });
    setProject((p) =>
      p ? { ...p, status: 'RENDER_REQUESTED', latest_render_id: created.render_id } : p,
    );
  }

  function handleProjectPatch(patch: Partial<Project>) {
    setProject((p) => (p ? { ...p, ...patch } : p));
  }

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
        {project?.status === 'FAILED' && project.error_code !== 'MODERATION_BLOCKED' && (
          <p className="error">
            分析失敗，請重新上傳影片。{project.error_code} {project.error_message}
          </p>
        )}
        {project && (
          <ModerationBanner
            project={project}
            onOverridden={(s: ModerationStatus) =>
              setProject((p) => (p ? { ...p, moderation_status: s } : p))
            }
          />
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
            render={render}
            onProjectPatch={handleProjectPatch}
            onRenderStarted={handleRenderStarted}
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
