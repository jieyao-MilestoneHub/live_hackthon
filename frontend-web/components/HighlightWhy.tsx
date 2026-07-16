'use client';

import { useState } from 'react';
import type { Highlight, HighlightSignal } from '@/types';
import { formatMs } from '@/lib/format';

const SIGNAL_LABEL: Record<HighlightSignal, string> = {
  chat_volume: '彈幕熱度',
  speech_emotion: '語音情緒',
  fusion: '融合訊號',
};

const FACET_LABEL: Record<string, string> = {
  keyword: '關鍵字',
  emoji: '表情',
  punctuation: '標點',
  volume: '音量',
};

/**
 * Expandable "為何入選" panel — surfaces the explainability fields the backend
 * already returns (emotion facets, detection volume-vs-threshold, raw-vs-corrected
 * window) so a highlight is never an unexplained black box. Renders nothing when
 * none of those fields are present (e.g. a bare transcribe result).
 */
export default function HighlightWhy({ highlight }: { highlight: Highlight }) {
  const [open, setOpen] = useState(false);
  const { emotion, detection, chat_window, signal, correction } = highlight;
  if (!emotion && !detection && !chat_window) return null;

  const facets = emotion?.breakdown
    ? (Object.entries(emotion.breakdown).filter(([, v]) => typeof v === 'number') as [string, number][])
    : [];
  const maxFacet = facets.reduce((m, [, v]) => Math.max(m, v), 0) || 1;

  // Volume-vs-threshold scale (headroom so both the bar and marker are visible).
  const vol = detection?.minute_volume;
  const thr = detection?.threshold;
  const scale = Math.max(vol ?? 0, thr ?? 0) * 1.25 || 1;

  return (
    <div className={`whycard${open ? ' is-open' : ''}`}>
      <button type="button" className="whycard__toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>為何入選</span>
        {signal && <span className="tag tag--tide">{SIGNAL_LABEL[signal]}</span>}
        <span className="whycard__caret" aria-hidden>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="whycard__body">
          {detection && vol != null && thr != null && (
            <div className="whyrow">
              <span className="whyrow__k mono">熱區偵測</span>
              <div className="whybar" title={`音量 ${vol} · 門檻 ${thr}`}>
                <span className="whybar__fill" style={{ width: `${Math.min(100, (vol / scale) * 100)}%` }} />
                <span className="whybar__mark" style={{ left: `${Math.min(100, (thr / scale) * 100)}%` }} />
              </div>
              <span className="whyrow__v mono">音量 {vol} / 門檻 {thr}</span>
            </div>
          )}

          {facets.length > 0 && (
            <div className="whyrow whyrow--facets">
              <span className="whyrow__k mono">情緒分項</span>
              <div className="whyfacets">
                {facets.map(([k, v]) => (
                  <div key={k} className="whyfacet">
                    <span className="whyfacet__label cjk">{FACET_LABEL[k] ?? k}</span>
                    <div className="whybar whybar--sm">
                      <span className="whybar__fill" style={{ width: `${(v / maxFacet) * 100}%` }} />
                    </div>
                    <span className="whyfacet__v mono">{v.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {chat_window && (
            <div className="whyrow">
              <span className="whyrow__k mono">窗位修正</span>
              <span className="whyrow__v mono cjk">
                原始 {formatMs(chat_window.start_ms)}–{formatMs(chat_window.end_ms)}
                {correction?.applied && correction.offset_ms != null && correction.offset_ms !== 0 && (
                  <> · 修正 {correction.offset_ms > 0 ? '+' : ''}{Math.round(correction.offset_ms / 1000)}s → {formatMs(highlight.start_ms)}–{formatMs(highlight.end_ms)}</>
                )}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
