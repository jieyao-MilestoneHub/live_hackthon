'use client';

import Link from 'next/link';
import { useRef } from 'react';
import type { BatchItemState, BatchItemStatus } from '@/lib/batchUpload';

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
  done: '已送出',
  failed: '失敗',
};

/** Example 剪接指令 — click to append to the prompt. */
const PROMPT_CHIPS = ['把爆點放大', '節奏快一點', '關鍵字加動畫', '多一點轉場', '只留最好笑的'];

export interface CardItem {
  video: File;
  log?: File;
  prompt?: string;
}

/**
 * One "剪輯需求卡": a video (required) + an optional paired chat LOG + an optional
 * 剪接指令 prompt that drives the 指令版 track. Presentational — all edits are
 * emitted to the parent BatchComposer, which owns the item array.
 */
export default function EditRequestCard({
  index,
  item,
  state,
  running,
  onAttachLog,
  onClearLog,
  onPrompt,
  onRemove,
}: {
  index: number;
  item: CardItem;
  state?: BatchItemState;
  running: boolean;
  onAttachLog: (file: File) => void;
  onClearLog: () => void;
  onPrompt: (prompt: string) => void;
  onRemove: () => void;
}) {
  const logRef = useRef<HTMLInputElement>(null);
  const status = state?.status;
  const locked = running || status === 'done';

  function appendChip(text: string) {
    if (locked) return;
    const cur = (item.prompt ?? '').trim();
    onPrompt(cur ? `${cur}、${text}` : text);
  }

  return (
    <li className="reqcard">
      <div className="reqcard__head">
        <span className="reqcard__idx mono">#{index + 1}</span>
        <span className="reqcard__name cjk" title={item.video.name}>
          {item.video.name}
        </span>
        <span className="reqcard__size mono muted">{fmtSize(item.video.size)}</span>
        {!running && status !== 'done' && (
          <button type="button" className="linkbtn" onClick={onRemove} aria-label={`移除 ${item.video.name}`}>
            ✕
          </button>
        )}
      </div>

      {/* LOG slot — presence flips this item to the 彈幕熱度 (chat) source. */}
      <div className="reqcard__slot">
        <span className="reqcard__slot-key mono">LOG</span>
        {item.log ? (
          <>
            <span className="reqcard__slot-val mono">{item.log.name}</span>
            <span className="tag tag--tide">彈幕熱度</span>
            {!locked && (
              <button type="button" className="linkbtn" onClick={onClearLog}>
                移除
              </button>
            )}
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={locked}
              onClick={() => logRef.current?.click()}
            >
              ＋ 附聊天室 LOG（選填 .csv）
            </button>
            <span className="reqcard__slot-hint muted">未附＝用語音逐字稿分析</span>
          </>
        )}
        <input
          ref={logRef}
          type="file"
          accept=".csv,text/csv"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onAttachLog(f);
            e.target.value = '';
          }}
        />
      </div>

      {/* 剪接指令 — only feeds the 指令版 track. Blank → 指令版 uses the template. */}
      <div className="reqcard__prompt">
        <label htmlFor={`prompt-${index}`}>
          想怎麼剪？<span className="muted">（只餵給「指令版」，留白→套用預設模板）</span>
        </label>
        <textarea
          id={`prompt-${index}`}
          className="input reqcard__ta"
          rows={2}
          maxLength={280}
          placeholder="例：把最爆笑那段放大、關鍵字加動畫、節奏快一點"
          value={item.prompt ?? ''}
          disabled={locked}
          onChange={(e) => onPrompt(e.target.value)}
        />
        {!locked && (
          <div className="reqcard__chips">
            {PROMPT_CHIPS.map((c) => (
              <button type="button" key={c} className="chip" onClick={() => appendChip(c)}>
                ＋ {c}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Per-item upload status (during/after submit). */}
      {state && status !== 'queued' && (
        <div className="reqcard__status">
          <div className="bar">
            <span
              style={{
                width: `${status === 'done' ? 100 : state.pct}%`,
                opacity: status === 'failed' ? 0.4 : 1,
              }}
            />
          </div>
          <div className="reqcard__status-meta mono muted">
            <span className={`reqcard__status-label reqcard__status-label--${status}`}>
              {status === 'uploading' ? `上傳中 ${state.pct}%` : STATUS_LABEL[status ?? 'queued']}
              {status !== 'failed' && state.analysisSource && (
                <span className="muted"> · {state.analysisSource === 'chat' ? '彈幕' : '逐字稿'}</span>
              )}
            </span>
            {status === 'done' && state.projectId && (
              <Link href={`/batch?ids=${encodeURIComponent(state.projectId)}`}>看進度 ▸</Link>
            )}
            {status === 'failed' && state.error && (
              <span className="reqcard__err" title={state.error}>
                {state.error}
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}
