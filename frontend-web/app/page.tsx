'use client';

import { useState } from 'react';
import HighlightWave from '@/components/HighlightWave';
import BatchComposer from '@/components/BatchComposer';

const MAX_TARGET_SEC = 60;
const DEFAULT_TARGET_SEC = 30;

const RULER = ['0:00', '1:30', '3:00', '4:00', '5:30'];

const STEPS = [
  {
    t: '批次配對上傳',
    d: '每支影片一張需求卡：影片、選填聊天室 LOG、選填剪接指令，一次送多支。',
  },
  {
    t: 'AI 分析 ＋ 雙軌渲染',
    d: '一次分析找出情緒高峰，接著同時產出「模板版」與「指令版」兩支成品。',
  },
  {
    t: '全程看得見',
    d: '每一步都有 AI 進度旁白，成品各附「這版做了什麼」，不是黑盒子。',
  },
];

export default function LandingPage() {
  const [title, setTitle] = useState('');
  const [targetSec, setTargetSec] = useState(DEFAULT_TARGET_SEC);

  const on = (n: number) => `reveal stagger-${n}`;
  const targetMs = Math.round(Math.min(MAX_TARGET_SEC, Math.max(1, targetSec)) * 1000);

  return (
    <main className="shell page">
      <section className="hero">
        <p className={`eyebrow ${on(1)}`}>AI HIGHLIGHT EDITOR · 直播高光剪輯</p>
        <h1 className={`hero__title cjk ${on(2)}`}>
          把<span className="grad">最猛的那幾秒</span>，
          <br />
          剪成兩版短片。
        </h1>
        <p className={`lead ${on(3)}`} style={{ maxWidth: 580 }}>
          上傳直播錄影，AI 找出情緒高峰、自動組出 60 秒內的精華，並同時產出「模板版」與「依你指令客製的指令版」——每一步都看得見。
        </p>

        <div className={`${on(4)}`} style={{ marginTop: 32 }}>
          <HighlightWave mode="draw" height={140} />
          <div
            className="mono muted"
            style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginTop: 4 }}
          >
            {RULER.map((r) => (
              <span key={r}>{r}</span>
            ))}
          </div>
        </div>
      </section>

      <div className="hero__grid">
        {/* Global settings for the whole batch */}
        <div className={`panel ${on(3)}`}>
          <div className="panel__head">
            <span className="panel__title">全批設定</span>
            <span className="panel__eyebrow">BATCH SETTINGS</span>
          </div>
          <div className="field">
            <label htmlFor="title">專案標題前綴（選填）</label>
            <input
              id="title"
              className="input"
              type="text"
              placeholder="例如：巔峰對決"
              value={title}
              maxLength={60}
              onChange={(e) => setTitle(e.target.value)}
            />
            <p className="hint">每支影片會命名為「前綴 — 檔名」。</p>
          </div>

          <div className="field">
            <label htmlFor="target">目標秒數（1–{MAX_TARGET_SEC} 秒，套用到整批）</label>
            <div className="row" style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <input
                id="target"
                type="range"
                min={1}
                max={MAX_TARGET_SEC}
                value={targetSec}
                onChange={(e) => setTargetSec(Number(e.target.value))}
                style={{ flex: 1 }}
              />
              <input
                className="input num"
                type="number"
                min={1}
                max={MAX_TARGET_SEC}
                value={targetSec}
                onChange={(e) => setTargetSec(Number(e.target.value))}
                aria-label="目標秒數"
              />
            </div>
            <p className="hint">
              = <span className="mono">{targetMs}</span> ms（契約以毫秒為單位）
            </p>
          </div>
        </div>

        {/* How it works — a real 3-step sequence */}
        <div className={`panel ${on(4)}`}>
          <div className="panel__head">
            <span className="panel__title">如何運作</span>
            <span className="panel__eyebrow">HOW IT WORKS</span>
          </div>
          <div className="steps">
            {STEPS.map((s, i) => (
              <div className="step" key={s.t}>
                <span className="step__num">{i + 1}</span>
                <div className="step__body">
                  <h3 className="cjk">{s.t}</h3>
                  <p>{s.d}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className={on(4)} style={{ marginTop: 20 }}>
        <BatchComposer targetDurationMs={targetMs} titlePrefix={title.trim() || undefined} />
      </div>
    </main>
  );
}
