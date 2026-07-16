'use client';

import { useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  MAX_BATCH_FILES,
  MAX_UPLOAD_BYTES,
  fileId,
  reconcilePairs,
  runBatchUpload,
  splitPicked,
  type BatchItemState,
  type BatchItemStatus,
  type ChatUploadPair,
} from '@/lib/batchUpload';

const MB = 1024 * 1024;
const GB = 1024 ** 3;

function fmtSize(bytes: number): string {
  return bytes >= GB ? `${(bytes / GB).toFixed(2)} GB` : `${Math.round(bytes / MB)} MB`;
}

const STATUS_LABEL: Record<BatchItemStatus, string> = {
  queued: '排隊中',
  creating: '建立專案…',
  uploading: '上傳中',
  completing: '完成中…',
  linking: '連結影片時長…',
  starting: '上傳 LOG，啟動分析…',
  done: '完成',
  failed: '失敗',
};

/**
 * Paired batch uploader. Every upload item is a VIDEO + chat-room LOG CSV pair;
 * each pair becomes its own chat Project and uploads via S3 multipart, then the
 * LOG PUT auto-triggers analysis + render. Videos and LOGs are dropped together
 * and auto-paired by filename; unmatched videos are flagged and fixed via a
 * per-row LOG picker. Bounded parallelism (3 pairs × 2 parts).
 */
export default function BatchUploader({
  targetDurationMs,
  titlePrefix,
}: {
  targetDurationMs: number;
  titlePrefix?: string;
}) {
  const [videos, setVideos] = useState<File[]>([]);
  const [logs, setLogs] = useState<File[]>([]);
  const [manual, setManual] = useState<Record<string, string>>({});
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [items, setItems] = useState<Record<string, BatchItemState>>({});
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const { pairs, unpairedVideos, unpairedLogs } = useMemo(
    () => reconcilePairs(videos, logs, manual),
    [videos, logs, manual],
  );
  const allPaired = pairs.length > 0 && pairs.every((p) => p.log);
  const unpairedLogIds = useMemo(() => new Set(unpairedLogs.map(fileId)), [unpairedLogs]);
  const totalBytes = useMemo(
    () => [...videos, ...logs].reduce((s, f) => s + f.size, 0),
    [videos, logs],
  );

  function addFiles(picked: File[]) {
    if (running) return;
    const { videos: nv, logs: nl, rejected: rej } = splitPicked(picked, videos.length, MAX_BATCH_FILES);
    if (rej.length) {
      setRejected((r) => [...r, ...rej.map((x) => ({ name: x.file.name, reason: x.reason }))]);
    }
    if (nv.length) setVideos((v) => [...v, ...nv]);
    if (nl.length) setLogs((l) => [...l, ...nl]);
    setFinished(false);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer?.files?.length) addFiles(Array.from(e.dataTransfer.files));
  }

  function removeVideo(video: File) {
    if (running) return;
    const id = fileId(video);
    setVideos((v) => v.filter((f) => fileId(f) !== id));
    setManual((m) => {
      const n = { ...m };
      delete n[id];
      return n;
    });
    setItems((it) => {
      const n = { ...it };
      delete n[id];
      return n;
    });
    setFinished(false);
  }

  function removeLog(log: File) {
    if (running) return;
    const id = fileId(log);
    setLogs((l) => l.filter((f) => fileId(f) !== id));
    setManual((m) => {
      const n: Record<string, string> = {};
      for (const [k, v] of Object.entries(m)) if (v !== id) n[k] = v;
      return n;
    });
    setFinished(false);
  }

  function assign(video: File, val: string) {
    const id = fileId(video);
    setManual((m) => {
      const n = { ...m };
      if (val === 'auto') delete n[id];
      else n[id] = val;
      return n;
    });
    setFinished(false);
  }

  function reset() {
    if (running) return;
    setVideos([]);
    setLogs([]);
    setManual({});
    setRejected([]);
    setItems({});
    setFinished(false);
  }

  async function startUpload() {
    const chatPairs: ChatUploadPair[] = pairs
      .filter((p) => p.log)
      .map((p) => ({ video: p.video, log: p.log as File, prompt: prompts[fileId(p.video)] }));
    if (!chatPairs.length || !allPaired || running) return;
    setRunning(true);
    setFinished(false);
    const init: Record<string, BatchItemState> = {};
    for (const p of chatPairs) init[fileId(p.video)] = { status: 'queued', pct: 0 };
    setItems(init);
    const results = await runBatchUpload(
      chatPairs,
      { target_duration_ms: targetDurationMs, titlePrefix },
      (index, patch) => {
        const vid = fileId(chatPairs[index].video);
        setItems((prev) => ({ ...prev, [vid]: { ...(prev[vid] ?? { status: 'queued', pct: 0 }), ...patch } }));
      },
      // partConcurrency omitted → runBatchUpload adapts it to the file count so a
      // single/few large files fill the ~6-PUT budget instead of crawling at 2.
      { fileConcurrency: 3 },
    );
    // Funnel to the dual-track dashboard to watch each video produce two versions.
    const ids = results.filter((r) => r.ok && r.projectId).map((r) => r.projectId as string);
    if (ids.length) {
      router.push(`/batch?ids=${ids.map(encodeURIComponent).join(',')}`);
      return;
    }
    setRunning(false);
    setFinished(true);
  }

  const summary = useMemo(() => {
    const vals = Object.values(items);
    return {
      done: vals.filter((i) => i.status === 'done').length,
      failed: vals.filter((i) => i.status === 'failed').length,
    };
  }, [items]);

  return (
    <div className="batch">
      {/* Dual-track explainer — teach the two outputs up front so results aren't confusing. */}
      <div className="twotrack">
        <p className="twotrack__lead cjk">每支影片會同時產出<strong className="grad"> 兩個版本</strong>：</p>
        <div className="twotrack__row">
          <span className="tag tag--tide">模板版</span>
          <span className="cjk">規則式、穩定，不需要指令</span>
        </div>
        <div className="twotrack__row">
          <span className="tag tag--crest">指令版</span>
          <span className="cjk">AI 依你在每張卡片填的文字客製；留白 → 也套用預設模板</span>
        </div>
      </div>

      <label
        className={`dropzone${dragOver ? ' is-drag' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!running) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/*,.csv,text/csv"
          multiple
          disabled={running}
          onChange={(e) => {
            if (e.target.files?.length) addFiles(Array.from(e.target.files));
            e.target.value = ''; // allow re-picking the same file
          }}
        />
        <strong className="cjk">拖曳或點選影片與聊天室 LOG CSV（可多選）</strong>
        <p className="hint" style={{ marginTop: 6 }}>
          每支影片需配一份 LOG（.csv）· 系統自動依檔名配對 · 單檔上限 {MAX_UPLOAD_BYTES / GB}GB · 每批最多{' '}
          {MAX_BATCH_FILES} 對
        </p>
      </label>

      {rejected.length > 0 && (
        <div className="error" style={{ marginTop: 10 }}>
          {rejected.map((r, i) => (
            <div key={`${r.name}-${i}`}>
              略過 <span className="mono">{r.name}</span>：{r.reason}
            </div>
          ))}
        </div>
      )}

      {videos.length > 0 && (
        <>
          <div
            className="mono muted"
            style={{ display: 'flex', justifyContent: 'space-between', marginTop: 14, fontSize: 12 }}
          >
            <span>
              {pairs.length} 對 · 共 {fmtSize(totalBytes)}
            </span>
            {finished ? (
              <span>
                完成 {summary.done} · 失敗 {summary.failed}
              </span>
            ) : (
              !running && (
                <button type="button" className="linkbtn" onClick={reset}>
                  清空
                </button>
              )
            )}
          </div>

          <ul className="batch__list">
            {pairs.map((pair) => {
              const video = pair.video;
              const vid = fileId(video);
              const it = items[vid] ?? { status: 'queued' as const, pct: 0 };
              const hasManual = Object.prototype.hasOwnProperty.call(manual, vid);
              const rowValue = hasManual ? manual[vid] || 'auto' : 'auto';
              const candidates = logs.filter(
                (l) => unpairedLogIds.has(fileId(l)) || (pair.log && fileId(pair.log) === fileId(l)),
              );
              return (
                <li className="batch__row" key={vid}>
                  <div className="batch__row-head">
                    <span className="batch__name cjk" title={video.name}>
                      {video.name}
                    </span>
                    {(running || finished) && (
                      <span className={`batch__status batch__status--${it.status}`}>
                        {it.status === 'uploading' ? `${it.pct}%` : STATUS_LABEL[it.status]}
                      </span>
                    )}
                    {!running && !finished && (
                      <button
                        type="button"
                        className="linkbtn"
                        onClick={() => removeVideo(video)}
                        aria-label={`移除 ${video.name}`}
                      >
                        ✕
                      </button>
                    )}
                  </div>

                  {!running && !finished ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                      <span
                        style={{
                          fontSize: 12,
                          whiteSpace: 'nowrap',
                          color: pair.log ? 'var(--ok, #3ba55d)' : 'var(--err, #e5484d)',
                        }}
                      >
                        {pair.log ? '✔ LOG' : '⚠ 缺 LOG'}
                      </span>
                      <select
                        className="input"
                        style={{ flex: 1, fontSize: 12 }}
                        value={rowValue}
                        onChange={(e) => assign(video, e.target.value)}
                        aria-label={`為 ${video.name} 選擇 LOG`}
                      >
                        <option value="auto">
                          自動配對{pair.log && !hasManual ? `（${pair.log.name}）` : '（無符合，請指定）'}
                        </option>
                        {candidates.map((l) => (
                          <option key={fileId(l)} value={fileId(l)}>
                            {l.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : (
                    <div className="batch__meta mono muted" style={{ marginTop: 6 }}>
                      {pair.log ? `LOG · ${pair.log.name}` : '（無 LOG）'}
                    </div>
                  )}

                  {!running && !finished && (
                    <textarea
                      className="input prompt-ta"
                      rows={2}
                      maxLength={280}
                      placeholder="想怎麼剪？（只餵給「指令版」，留白→套用預設模板）"
                      value={prompts[vid] ?? ''}
                      onChange={(e) => setPrompts((p) => ({ ...p, [vid]: e.target.value }))}
                      aria-label={`${video.name} 的剪接指令`}
                    />
                  )}
                  {(running || finished) && prompts[vid]?.trim() && (
                    <div className="batch__meta mono muted" style={{ marginTop: 6 }}>
                      指令版指令：{prompts[vid].trim()}
                    </div>
                  )}

                  {(running || finished) && (
                    <div className="bar" style={{ marginTop: 6 }}>
                      <span
                        style={{
                          width: `${it.status === 'done' ? 100 : it.pct}%`,
                          opacity: it.status === 'failed' ? 0.4 : 1,
                        }}
                      />
                    </div>
                  )}
                  <div className="batch__meta mono muted">
                    <span>{fmtSize(video.size)}</span>
                    {it.status === 'done' && it.projectId && (
                      <Link href={`/projects?id=${encodeURIComponent(it.projectId)}`}>開啟專案 ▸</Link>
                    )}
                    {it.status === 'failed' && it.error && (
                      <span className="batch__err" title={it.error}>
                        {it.error}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {unpairedLogs.length > 0 && !running && !finished && (
        <div style={{ marginTop: 12 }}>
          <p className="hint">
            未配對 LOG（{unpairedLogs.length}）· 於上方影片列指定，或補上同名影片：
          </p>
          <ul className="batch__list">
            {unpairedLogs.map((l) => (
              <li className="batch__row" key={fileId(l)}>
                <div className="batch__row-head">
                  <span className="batch__name mono" title={l.name}>
                    {l.name}
                  </span>
                  <button
                    type="button"
                    className="linkbtn"
                    onClick={() => removeLog(l)}
                    aria-label={`移除 ${l.name}`}
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        type="button"
        className="btn btn--lg btn--block"
        style={{ marginTop: 16 }}
        disabled={running || !allPaired}
        onClick={finished ? reset : startUpload}
      >
        {running ? '上傳中…' : finished ? '重新選擇檔案' : `建立並批次上傳 ${pairs.length || ''} 對 ▸`}
      </button>
      {!allPaired && videos.length > 0 && !running && !finished && (
        <p className="hint" style={{ marginTop: 8, color: 'var(--err, #e5484d)' }}>
          尚有 {unpairedVideos.length} 支影片未配 LOG，指定後即可開始。
        </p>
      )}
      {finished && (
        <p className="hint" style={{ marginTop: 10 }}>
          {summary.failed > 0
            ? `有 ${summary.failed} 對失敗，可重試（每次重試會建立新專案）。`
            : '全部完成！點各專案的「開啟專案」查看分析進度。'}
        </p>
      )}
    </div>
  );
}
