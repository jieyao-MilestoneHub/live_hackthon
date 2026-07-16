'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  MAX_BATCH_FILES,
  MAX_UPLOAD_BYTES,
  partitionFiles,
  runBatchUpload,
  type BatchItemState,
} from '@/lib/batchUpload';
import type { CardItem } from './EditRequestCard';
import EditRequestCard from './EditRequestCard';

const GB = 1024 ** 3;

/**
 * Unified batch composer. Each item is a 剪輯需求卡 = video (required) + optional
 * chat LOG + optional 剪接指令. On submit every item becomes its own Project and
 * uploads (bounded concurrency), then the user is taken to the batch dashboard
 * to watch each video produce two versions (模板版 + 指令版).
 */
export default function BatchComposer({
  targetDurationMs,
  titlePrefix,
}: {
  targetDurationMs: number;
  titlePrefix?: string;
}) {
  const router = useRouter();
  const [items, setItems] = useState<CardItem[]>([]);
  const [states, setStates] = useState<BatchItemState[]>([]);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const addVideos = useCallback(
    (picked: File[]) => {
      if (running) return;
      setItems((prev) => {
        const { accepted, rejected: rej } = partitionFiles(
          picked.map((f) => f),
          prev.length,
        );
        if (rej.length) {
          setRejected((r) => [...r, ...rej.map((x) => ({ name: x.file.name, reason: x.reason }))]);
        }
        if (!accepted.length) return prev;
        setStates((s) => [...s, ...accepted.map(() => ({ status: 'queued' as const, pct: 0 }))]);
        return [...prev, ...accepted.map((video) => ({ video }))];
      });
    },
    [running],
  );

  const patchItem = (i: number, patch: Partial<CardItem>) =>
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));

  const removeItem = (i: number) => {
    if (running) return;
    setItems((prev) => prev.filter((_, idx) => idx !== i));
    setStates((prev) => prev.filter((_, idx) => idx !== i));
  };

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer?.files?.length) addVideos(Array.from(e.dataTransfer.files));
    },
    [addVideos],
  );

  async function startUpload() {
    if (!items.length || running) return;
    setError(null);
    setRunning(true);
    setStates(items.map(() => ({ status: 'queued', pct: 0 })));
    const results = await runBatchUpload(
      items,
      { target_duration_ms: targetDurationMs, titlePrefix },
      (index, patch) =>
        setStates((prev) => {
          const next = prev.slice();
          next[index] = { ...next[index], ...patch };
          return next;
        }),
      { fileConcurrency: 3, partConcurrency: 2 },
    );
    const ids = results.filter((r) => r.ok && r.projectId).map((r) => r.projectId as string);
    if (ids.length) {
      router.push(`/batch?ids=${ids.map(encodeURIComponent).join(',')}`);
      return;
    }
    setRunning(false);
    setError('全部項目上傳失敗，請檢查檔案後重試。');
  }

  const totalBytes = useMemo(() => items.reduce((s, it) => s + it.video.size, 0), [items]);
  const fmtTotal = totalBytes >= GB ? `${(totalBytes / GB).toFixed(2)} GB` : `${Math.round(totalBytes / (1024 * 1024))} MB`;

  return (
    <div className="panel composer">
      <div className="panel__head">
        <span className="panel__title cjk">批次剪輯需求</span>
        <span className="panel__eyebrow">BATCH · {items.length}/{MAX_BATCH_FILES}</span>
      </div>

      {/* Dual-track explainer — teach the two outputs up front so results aren't confusing. */}
      <div className="twotrack">
        <p className="twotrack__lead cjk">每支影片會同時產出<strong className="grad"> 兩個版本</strong>：</p>
        <div className="twotrack__row">
          <span className="tag tag--tide">模板版</span>
          <span className="cjk">規則式、穩定，不需要指令</span>
        </div>
        <div className="twotrack__row">
          <span className="tag tag--crest">指令版</span>
          <span className="cjk">AI 依你在卡片裡的文字客製；留白 → 也套用預設模板</span>
        </div>
      </div>

      <label
        className={`dropzone${dragOver ? ' is-drag' : ''}`}
        style={{ marginTop: 14 }}
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
          accept="video/*"
          multiple
          disabled={running}
          onChange={(e) => {
            if (e.target.files?.length) addVideos(Array.from(e.target.files));
            e.target.value = '';
          }}
        />
        <strong className="cjk">{items.length ? '＋ 再加影片（可多選）' : '拖曳或點選影片檔案（可多選）'}</strong>
        <p className="hint" style={{ marginTop: 6 }}>
          每支各自建立專案 · 單檔上限 {MAX_UPLOAD_BYTES / GB}GB · 每批最多 {MAX_BATCH_FILES} 支 · LOG 與指令逐卡填
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

      {items.length > 0 && (
        <ul className="reqcard__list">
          {items.map((item, i) => (
            <EditRequestCard
              key={`${item.video.name}-${i}`}
              index={i}
              item={item}
              state={states[i]}
              running={running}
              onAttachLog={(file) => patchItem(i, { log: file })}
              onClearLog={() => patchItem(i, { log: undefined })}
              onPrompt={(prompt) => patchItem(i, { prompt })}
              onRemove={() => removeItem(i)}
            />
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <div className="composer__foot mono muted">
          {items.length} 支 · 共 {fmtTotal} · {items.filter((i) => i.log).length} 支附 LOG · {items.filter((i) => (i.prompt ?? '').trim()).length} 支有指令
        </div>
      )}

      <button
        type="button"
        className="btn btn--lg btn--block"
        style={{ marginTop: 14 }}
        disabled={running || !items.length}
        onClick={startUpload}
      >
        {running ? '上傳中…' : `建立並批次處理 ${items.length || ''} 支影片 ▸`}
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
