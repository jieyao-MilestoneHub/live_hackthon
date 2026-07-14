'use client';

// The signature motif: a live-stream energy curve where peaks = highlights.
// Reused across the site — hero (draw), processing (scan), timeline (static) —
// always carrying meaning (where the energy peaks are), never pure decoration.

import { useEffect, useId, useMemo, useRef, useState } from 'react';

export interface WavePeak {
  /** horizontal position 0..1 */
  x: number;
  /** relative strength 0..1 (bump height + marker) */
  s: number;
}

const DEFAULT_PEAKS: WavePeak[] = [
  { x: 0.16, s: 0.5 },
  { x: 0.4, s: 0.92 },
  { x: 0.62, s: 0.66 },
  { x: 0.85, s: 1 },
];

const W = 1000;
const N = 72;

function amplitudeAt(t: number, peaks: WavePeak[]): number {
  let a = 0.12 + 0.06 * Math.sin(t * Math.PI * 9);
  for (const p of peaks) {
    const d = t - p.x;
    a += p.s * 0.9 * Math.exp(-(d * d) / (2 * 0.045 * 0.045));
  }
  return Math.max(0, Math.min(1, a));
}

/** Catmull-Rom → cubic-bezier smoothing for an organic curve. */
function smoothPath(pts: { x: number; y: number }[]): string {
  if (pts.length < 2) return '';
  let d = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(
      2,
    )}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
  }
  return d;
}

export default function HighlightWave({
  mode = 'static',
  height = 130,
  peaks = DEFAULT_PEAKS,
  className,
}: {
  mode?: 'draw' | 'scan' | 'static';
  height?: number;
  peaks?: WavePeak[];
  className?: string;
}) {
  const uid = useId().replace(/:/g, '');
  const pathRef = useRef<SVGPathElement>(null);
  const [len, setLen] = useState(0);
  const [drawn, setDrawn] = useState(mode !== 'draw');

  const { line, area, markers } = useMemo(() => {
    const baseline = height * 0.66;
    const maxAmp = height * 0.52;
    const pts = Array.from({ length: N }, (_, i) => {
      const t = i / (N - 1);
      return { x: t * W, y: baseline - amplitudeAt(t, peaks) * maxAmp };
    });
    const line = smoothPath(pts);
    const area = `${line} L ${W} ${height} L 0 ${height} Z`;
    const markers = peaks.map((p) => ({
      x: p.x * W,
      y: baseline - amplitudeAt(p.x, peaks) * maxAmp,
      s: p.s,
    }));
    return { line, area, markers };
  }, [height, peaks]);

  useEffect(() => {
    if (mode !== 'draw') return;
    const reduce =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const p = pathRef.current;
    if (p) setLen(p.getTotalLength());
    if (reduce) {
      setDrawn(true);
      return;
    }
    const raf = requestAnimationFrame(() => setDrawn(true));
    return () => cancelAnimationFrame(raf);
  }, [mode, line]);

  const drawDuration = 1.15;

  return (
    <div
      className={className}
      style={{ position: 'relative', width: '100%', height }}
      aria-hidden="true"
    >
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${W} ${height}`}
        preserveAspectRatio="none"
        style={{ display: 'block', overflow: 'visible' }}
      >
        <defs>
          <linearGradient id={`stroke-${uid}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#ff3d71" />
            <stop offset="50%" stopColor="#8b5cff" />
            <stop offset="100%" stopColor="#29e3f0" />
          </linearGradient>
          <linearGradient id={`fill-${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(255,61,113,0.22)" />
            <stop offset="100%" stopColor="rgba(41,227,240,0)" />
          </linearGradient>
        </defs>

        <path d={area} fill={`url(#fill-${uid})`} stroke="none" />
        <path
          ref={pathRef}
          d={line}
          fill="none"
          stroke={`url(#stroke-${uid})`}
          strokeWidth={3}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
          style={
            mode === 'draw' && len
              ? {
                  strokeDasharray: len,
                  strokeDashoffset: drawn ? 0 : len,
                  transition: `stroke-dashoffset ${drawDuration}s var(--ease, ease)`,
                }
              : undefined
          }
        />

        {/* scan sweep */}
        {mode === 'scan' && (
          <rect
            x={-160}
            y={0}
            width={160}
            height={height}
            fill={`url(#stroke-${uid})`}
            opacity={0.14}
            style={{ animation: 'waveScan 1.9s linear infinite' }}
          />
        )}

        {/* highlight peak markers */}
        {markers.map((m, i) => (
          <g
            key={i}
            style={{
              opacity: drawn ? 1 : 0,
              transform: drawn ? 'scale(1)' : 'scale(0.4)',
              transformOrigin: `${m.x}px ${m.y}px`,
              transition: `opacity 0.4s ease ${
                mode === 'draw' ? m.x / W * drawDuration : 0
              }s, transform 0.5s var(--ease, ease) ${
                mode === 'draw' ? m.x / W * drawDuration : 0
              }s`,
            }}
          >
            <circle cx={m.x} cy={m.y} r={7} fill="rgba(255,61,113,0.18)" />
            <circle cx={m.x} cy={m.y} r={3.4} fill="#ff3d71" />
          </g>
        ))}
      </svg>

      <style>{`@keyframes waveScan{0%{transform:translateX(0)}100%{transform:translateX(${
        W + 200
      }px)}}`}</style>
    </div>
  );
}
