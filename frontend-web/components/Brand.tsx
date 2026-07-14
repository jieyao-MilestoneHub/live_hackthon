// 浪 LIVE logotype — original mark (wave-gradient 浪 + LIVE wordmark).
// Not a reproduction of any third party's logo.

export default function Brand({ size }: { size?: 'lg' }) {
  return (
    <span className={`brand${size === 'lg' ? ' brand--lg' : ''}`}>
      <span className="brand__wave">浪</span>
      <span className="brand__word">LIVE</span>
    </span>
  );
}
