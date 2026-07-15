'use client';

// Lightweight Cognito login gate in the top bar. Optional by design — the demo
// works logged-out (backend auth is lenient); logging in just attaches a Bearer
// IdToken to API calls. Uses lib/auth.ts (plain fetch, no AWS SDK).

import { useEffect, useState } from 'react';
import { AUTH_EVENT, getUserEmail, isLoggedIn, login, logout } from '@/lib/auth';

export default function LoginWidget() {
  // Start logged-out on the server/first paint, then hydrate from sessionStorage
  // in an effect to avoid a hydration mismatch on static export.
  const [ready, setReady] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [emailInput, setEmailInput] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReady(true);
    const sync = () => setEmail(isLoggedIn() ? getUserEmail() : null);
    sync();
    window.addEventListener(AUTH_EVENT, sync);
    return () => window.removeEventListener(AUTH_EVENT, sync);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await login(emailInput.trim(), password);
      setEmail(res.email);
      setOpen(false);
      setPassword('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '登入失敗，請重試。');
    } finally {
      setLoading(false);
    }
  }

  function handleLogout() {
    logout();
    setEmail(null);
    setOpen(false);
  }

  // Render nothing until hydrated so SSR markup and client match.
  if (!ready) return null;

  if (email) {
    return (
      <div className="auth">
        <span className="pill pill--done" title={email}>
          <span className="pill__dot" />
          <span className="auth__user">{email}</span>
        </span>
        <button className="btn btn--ghost btn--sm" onClick={handleLogout}>
          登出
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <div className="auth">
        <button className="btn btn--ghost btn--sm" onClick={() => setOpen(true)}>
          登入
        </button>
      </div>
    );
  }

  return (
    <form className="auth auth__form" onSubmit={handleSubmit}>
      <input
        className="auth__input"
        type="email"
        placeholder="email"
        autoComplete="username"
        value={emailInput}
        onChange={(e) => setEmailInput(e.target.value)}
        aria-label="登入 email"
      />
      <input
        className="auth__input"
        type="password"
        placeholder="密碼"
        autoComplete="current-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        aria-label="登入密碼"
      />
      <button className="btn btn--sm" type="submit" disabled={loading || !emailInput || !password}>
        {loading ? '登入中…' : '登入 ▸'}
      </button>
      <button
        className="btn btn--ghost btn--sm"
        type="button"
        onClick={() => {
          setOpen(false);
          setError(null);
        }}
      >
        取消
      </button>
      {error && <span className="auth__err">{error}</span>}
    </form>
  );
}
