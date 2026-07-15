'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  MAX_BATCH_FILES,
  MAX_UPLOAD_BYTES,
  partitionFiles,
  runBatchUpload,
  type BatchItemState,
} from '@/lib/batchUpload';

const MB = 1024 * 1024;
const GB = 1024 ** 3;

function fmtSize(bytes: number): string {
  return bytes >= GB ? `${(bytes / GB).toFixed(2)} GB` : `${Math.round(bytes / MB)} MB`;
}

const STATUS_LABEL: Record<BatchItemState['status'], string> = {
  queued: '排隊中',
  creating: '建立專案…',
  uploading: '上傳中',
  completing: '完成中…',
  done: '完成',
  failed: '失敗',
};

/**
 * Unified batch uploader (transcribe pipeline). Every selected file becomes its
 * own Project and uploads via S3 multipart, with bounded parallelism (3 files ×
 * 2 parts). A single file is just a batch of one — this is the only upload path.
 */
export default function BatchUploader({
  targetDurationMs,
  titlePrefix,
}: {
  targetDurationMs: number;
  titlePrefix?: string;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [items, setItems] = useState<BatchItemState[]>([]);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback(
    (picked: File[]) => {
      if (running) return;
      setFiles((prev) => {
        const { accepted, rejected: rej } = partitionFiles(picked, prev.length);
        if (rej.length) {
          setRejected((r) => [...r, ...rej.map((x) => ({ name: x.file.name, reason: x.reason }))]);
        }
        if (!accepted.length) return prev;
        setItems((it) => [...it, ...accepted.map(() => ({ status: 'queued' as const, pct: 0 }))]);
        setFinished(false);
        return [...prev, ...accepted];
      });
    },
    [running],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer?.files?.length) addFiles(Array.from(e.dataTransfer.files));
    },
    [addFiles],
  );

  function removeFile(index: number) {
    if (running) return;
    setFiles((prev) => prev.filter((_, i) => i !== index));
    setItems((prev) => prev.filter((_, i) => i !== index));
  }

  function reset() {
    if (running) return;
    setFiles([]);
    setItems([]);
    setRejected([]);
    setFinished(false);
  }

  async function startUpload() {
    if (!files.length || running) return;
    setRunning(true);
    setFinished(false);
    setItems(files.map(() => ({ status: 'queued', pct: 0 })));
    await runBatchUpload(
      files,
      { target_duration_ms: targetDurationMs, analysis_source: 'transcribe', titlePrefix },
      (index, patch) =>
        setItems((prev) => {
          const nextItems = prev.slice();
          nextItems[index] = { ...nextItems[index], ...patch };
          return nextItems;
        }),
      { fileConcurrency: 3, partConcurrency: 2 },
    );
    setRunning(false);
    setFinished(true);
  }

  const summary = useMemo(() => {
    const done = items.filter((i) => i.status === 'done').length;
    const failed = items.filter((i) => i.status === 'failed').length;
    return { done, failed };
  }, [items]);

  const totalBytes = useMemo(() => files.reduce((s, f) => s + f.size, 0), [files]);

  return (
    <div className="batch">
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
          accept="video/*"
          multiple
          disabled={running}
          onChange={(e) => {
            if (e.target.files?.length) addFiles(Array.from(e.target.files));
            e.target.value = ''; // allow re-picking the same file
          }}
        />
        <strong className="cjk">拖曳或點選影片檔案（可多選）</strong>
        <p className="hint" style={{ marginTop: 6 }}>
          每支影片各自建立專案並分析 · 單檔上限 {MAX_UPLOAD_BYTES / GB}GB · 每批次最多 {MAX_BATCH_FILES} 檔
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

      {files.length > 0 && (
        <>
          <div
            className="mono muted"
            style={{ display: 'flex', justifyContent: 'space-between', marginTop: 14, fontSize: 12 }}
          >
            <span>
              {files.length} 檔 · 共 {fmtSize(totalBytes)}
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
            {files.map((file, i) => {
              const it = items[i] ?? { status: 'queued' as const, pct: 0 };
              return (
                <li className="batch__row" key={`${file.name}-${i}`}>
                  <div className="batch__row-head">
                    <span className="batch__name cjk" title={file.name}>
                      {file.name}
                    </span>
                    <span className={`batch__status batch__status--${it.status}`}>
                      {it.status === 'uploading' ? `${it.pct}%` : STATUS_LABEL[it.status]}
                    </span>
                    {!running && !finished && (
                      <button
                        type="button"
                        className="linkbtn"
                        onClick={() => removeFile(i)}
                        aria-label={`移除 ${file.name}`}
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  <div className="bar" style={{ marginTop: 6 }}>
                    <span
                      style={{
                        width: `${it.status === 'done' ? 100 : it.pct}%`,
                        opacity: it.status === 'failed' ? 0.4 : 1,
                      }}
                    />
                  </div>
                  <div className="batch__meta mono muted">
                    <span>{fmtSize(file.size)}</span>
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

      <button
        type="button"
        className="btn btn--lg btn--block"
        style={{ marginTop: 16 }}
        disabled={running || !files.length}
        onClick={startUpload}
      >
        {running
          ? '上傳中…'
          : finished
            ? '重新選擇檔案'
            : `建立並批次上傳 ${files.length || ''} 檔 ▸`}
      </button>
      {finished && (
        <p className="hint" style={{ marginTop: 10 }}>
          {summary.failed > 0
            ? `有 ${summary.failed} 檔失敗，可移除後重試（每次重試會建立新專案）。`
            : '全部完成！點各專案的「開啟專案」查看分析進度。'}
        </p>
      )}
    </div>
  );
}
