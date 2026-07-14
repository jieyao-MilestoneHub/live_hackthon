// Highlight score as mini wave-bars (echoes the Highlight Wave motif) + value.

const HEIGHTS = [6, 9, 12, 15, 18];

export default function ScoreMeter({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score));
  const filled = Math.round(pct * HEIGHTS.length);
  return (
    <span className="meter" title={`score ${pct.toFixed(2)}`}>
      {HEIGHTS.map((h, i) => (
        <span
          key={i}
          className={`meter__bar${i < filled ? ' is-on' : ''}`}
          style={{ height: h }}
        />
      ))}
      <span className="meter__val">{Math.round(pct * 100)}</span>
    </span>
  );
}
