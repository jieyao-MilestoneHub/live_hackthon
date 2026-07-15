// Minimal Amazon Cognito login for the 浪 LIVE editor — USER_PASSWORD_AUTH via a
// plain `fetch` POST to the Cognito IDP endpoint. No AWS SDK, no SRP, no extra
// dependency → static-export safe. The returned IdToken is kept in memory and
// mirrored to sessionStorage so it survives a page reload within the tab.
//
// Backend auth is currently lenient (API Gateway accepts anonymous), so login is
// OPTIONAL: lib/api.ts only attaches `Authorization: Bearer <IdToken>` when a
// token exists. Do NOT hard-require login for the demo.

const REGION = process.env.NEXT_PUBLIC_COGNITO_REGION || 'us-east-1';
const CLIENT_ID = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || '';

const TOKEN_KEY = 'lang_live_id_token';
const EMAIL_KEY = 'lang_live_user_email';

let inMemoryToken: string | null = null;
let inMemoryEmail: string | null = null;

export interface LoginResult {
  idToken: string;
  email: string;
  expiresIn?: number;
}

/** Cognito IDP JSON endpoint for the configured region. */
function endpoint(): string {
  return `https://cognito-idp.${REGION}.amazonaws.com/`;
}

function persist(token: string, email: string): void {
  inMemoryToken = token;
  inMemoryEmail = email;
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
    window.sessionStorage.setItem(EMAIL_KEY, email);
  } catch {
    /* sessionStorage unavailable — keep the in-memory copy only */
  }
}

/**
 * Log in via Cognito InitiateAuth (AuthFlow: USER_PASSWORD_AUTH).
 * On success stores + returns the IdToken. Throws with a readable message on
 * failure (bad credentials, unconfirmed user, or an unsupported challenge).
 */
export async function login(email: string, password: string): Promise<LoginResult> {
  if (!CLIENT_ID) {
    throw new Error('尚未設定 Cognito Client ID（NEXT_PUBLIC_COGNITO_CLIENT_ID）。');
  }
  const res = await fetch(endpoint(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
    },
    body: JSON.stringify({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: CLIENT_ID,
      AuthParameters: { USERNAME: email, PASSWORD: password },
    }),
  });

  const data: any = await res.json().catch(() => ({}));

  if (!res.ok) {
    // Cognito errors: { __type: "NotAuthorizedException", message: "..." }
    const detail: string = data?.message || data?.__type || `HTTP ${res.status}`;
    throw new Error(`登入失敗：${detail}`);
  }
  if (data?.ChallengeName) {
    // e.g. NEW_PASSWORD_REQUIRED — out of scope for the demo login.
    throw new Error(`此帳號需要額外驗證步驟（${data.ChallengeName}），請改用已設定密碼的帳號。`);
  }
  const idToken: string | undefined = data?.AuthenticationResult?.IdToken;
  if (!idToken) {
    throw new Error('登入回應缺少 IdToken。');
  }
  persist(idToken, email);
  return { idToken, email, expiresIn: data?.AuthenticationResult?.ExpiresIn };
}

/** Current IdToken (in-memory first, then sessionStorage), or null when logged out. */
export function getIdToken(): string | null {
  if (inMemoryToken) return inMemoryToken;
  if (typeof window === 'undefined') return null;
  try {
    inMemoryToken = window.sessionStorage.getItem(TOKEN_KEY);
    return inMemoryToken;
  } catch {
    return null;
  }
}

/** Email of the logged-in user, or null. */
export function getUserEmail(): string | null {
  if (inMemoryEmail) return inMemoryEmail;
  if (typeof window === 'undefined') return null;
  try {
    inMemoryEmail = window.sessionStorage.getItem(EMAIL_KEY);
    return inMemoryEmail;
  } catch {
    return null;
  }
}

export function isLoggedIn(): boolean {
  return !!getIdToken();
}

/** Decode a JWT payload without verifying the signature (display-only). */
function decodeJwtPayload(token: string): Record<string, any> {
  try {
    const payload = token.split('.')[1];
    const padded = payload.replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(padded));
  } catch {
    return {};
  }
}

/** True if the logged-in user is in the Cognito ``moderator``/``admin`` group.
 * Display-gating only — the backend re-checks the role on override. */
export function isModerator(): boolean {
  const token = getIdToken();
  if (!token) return false;
  const groups = decodeJwtPayload(token)['cognito:groups'];
  const list: string[] = Array.isArray(groups)
    ? groups
    : typeof groups === 'string'
      ? groups.split(',')
      : [];
  return list.map((g) => g.trim().toLowerCase()).some((g) => g === 'moderator' || g === 'admin');
}

/** Clear the stored token (both in-memory and sessionStorage). */
export function logout(): void {
  inMemoryToken = null;
  inMemoryEmail = null;
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
    window.sessionStorage.removeItem(EMAIL_KEY);
  } catch {
    /* ignore */
  }
}
