'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createProject } from '@/lib/api';
import HighlightWave from '@/components/HighlightWave';

const MAX_TARGET_SEC = 60;
const DEFAULT_TARGET_SEC = 30;

const RULER = ['0:00', '1:30', '3:00', '4:00', '5:30'];

const STEPS = [
  { t: '上傳原始影片', d: '瀏覽器以 presigned URL 直傳直播錄影至 S3，不經過伺服器。' },
  { t: 'AI 分析高光並自動組片', d: '偵測情緒高峰，Composer 依目標秒數組出初始時間軸。' },
  { t: '微調時間軸，一鍵渲染', d: '排序、鎖定、選字幕與特效比例，送出即輸出成品短片。' },
];

export default function LandingPage() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [targetSec, setTargetSec] = useState(DEFAULT_TARGET_SEC);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const on = (n: number) => `reveal stagger-${n}`;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (targetSec < 1 || targetSec > MAX_TARGET_SEC) {
      setError(`目標秒數需介於 1–${MAX_TARGET_SEC} 秒。`);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject({
        title: title.trim() || undefined,
        target_duration_ms: Math.round(targetSec * 1000),
      });
      router.push(`/projects?id=${encodeURIComponent(created.project_id)}`);
    } catch (err) {
      console.error(err);
      setError('建立專案失敗，請稍後再試。');
      setSubmitting(false);
    }
  }

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
          上傳直播錄影，AI 找出情緒高峰、自動組出 60 秒內的精華，時間軸交給你微調。
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
          <form onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="title">專案標題（選填）</label>
              <input
                id="title"
                className="input"
                type="text"
                placeholder="例如：巔峰對決精華"
                value={title}
                maxLength={80}
                onChange={(e) => setTitle(e.target.value)}
              />
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

            <button type="submit" className="btn btn--lg btn--block" disabled={submitting}>
              {submitting ? '建立中…' : '建立並開始上傳 ▸'}
            </button>
            {error && <p className="error">{error}</p>}
          </form>
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
