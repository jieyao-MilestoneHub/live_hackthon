'use client';

import { useState } from 'react';
import HighlightWave from '@/components/HighlightWave';
import BatchUploader from '@/components/BatchUploader';

const MAX_TARGET_SEC = 60;
const DEFAULT_TARGET_SEC = 30;

const RULER = ['0:00', '1:30', '3:00', '4:00', '5:30'];

const STEPS = [
  {
    t: '上傳影片＋聊天室 LOG',
    d: '瀏覽器以 presigned URL 直傳直播錄影至 S3；每支影片搭配一份聊天室 LOG CSV，系統自動依檔名配對。',
  },
  { t: 'AI 分析高光並自動組片', d: '從彈幕熱度峰值偵測高光，Composer 依目標秒數組出初始時間軸。' },
  { t: '微調時間軸，一鍵渲染', d: '排序、鎖定、選字幕與特效比例，送出即輸出成品短片。' },
];

export default function LandingPage() {
  const [title, setTitle] = useState('');
  const [targetSec, setTargetSec] = useState(DEFAULT_TARGET_SEC);

  const on = (n: number) => `reveal stagger-${n}`;

  return (
    <main className="shell page">
      <section className="hero">
        <p className={`eyebrow ${on(1)}`}>AI HIGHLIGHT EDITOR · 直播高光剪輯</p>
        <h1 className={`hero__title cjk ${on(2)}`}>
          把<span className="grad">最猛的那幾秒</span>，
          <br />
          剪成短片。
        </h1>
        <p className={`lead ${on(3)}`} style={{ maxWidth: 560 }}>
          批次上傳直播錄影與聊天室 LOG，AI 從彈幕熱度找出情緒高峰、自動組出 60 秒內的精華，時間軸交給你微調。
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
        {/* Create console */}
        <div className={`panel ${on(3)}`}>
          <div className="panel__head">
            <span className="panel__title">開始一個新專案</span>
            <span className="panel__eyebrow">NEW PROJECT</span>
          </div>
          <div>
            <div className="field">
              <label htmlFor="title">專案標題前綴（選填）</label>
              <input
                id="title"
                className="input"
                type="text"
                placeholder="例如：巔峰對決精華"
                value={title}
                maxLength={80}
                onChange={(e) => setTitle(e.target.value)}
              />
              <p className="hint">批次上傳時，各專案會以「前綴 — 檔名」命名。</p>
            </div>

            <div className="field">
              <label htmlFor="target">目標秒數（1–{MAX_TARGET_SEC} 秒）</label>
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
                = <span className="mono">{Math.round(targetSec * 1000)}</span> ms（契約以毫秒為單位）
              </p>
            </div>

            <p className="hint" style={{ marginBottom: 10 }}>
              每支影片需搭配一份聊天室 LOG（.csv），系統自動依檔名配對。
            </p>

            <BatchUploader
              targetDurationMs={Math.round(Math.min(MAX_TARGET_SEC, Math.max(1, targetSec)) * 1000)}
              titlePrefix={title.trim() || undefined}
            />
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
    </main>
  );
}
