'use client';

// Hard login gate: the app is unusable until a valid Cognito login. The API
// Gateway enforces a JWT authorizer (anonymous calls 401), so this also stops
// the UI from silently falling back to mock for a logged-out user — and stops
// anonymous use of the billable render pipeline.

import { useEffect, useState } from 'react';
import { AUTH_EVENT, isLoggedIn, login } from '@/lib/auth';

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => setAuthed(isLoggedIn());
    sync();
    setReady(true);
    window.addEventListener(AUTH_EVENT, sync);
    return () => window.removeEventListener(AUTH_EVENT, sync);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email.trim(), password);
      setAuthed(true);
      setPassword('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '登入失敗，請重試。');
    } finally {
      setLoading(false);
    }
  }

  // Avoid a hydration flash on static export: render nothing until we've checked.
  if (!ready) return null;
  if (authed) return <>{children}</>;

  return (
    <main className="shell page">
      <div className="panel" style={{ maxWidth: 440, margin: '48px auto' }}>
        <div className="panel__head">
          <span className="panel__title cjk">請先登入</span>
          <span className="panel__eyebrow">SIGN IN</span>
        </div>
        <p className="hint" style={{ marginTop: 0 }}>
          本服務需登入授權帳號才能使用（防止匿名濫用渲染資源）。
        </p>
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="ag-email">Email</label>
            <input
              id="ag-email"
              className="input"
              type="email"
              autoComplete="username"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ag-pass">密碼</label>
            <input
              id="ag-pass"
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button className="btn btn--lg btn--block" type="submit" disabled={loading || !email || !password}>
            {loading ? '登入中…' : '登入 ▸'}
          </button>
          {error && <p className="error">{error}</p>}
        </form>
      </div>
    </main>
  );
}
