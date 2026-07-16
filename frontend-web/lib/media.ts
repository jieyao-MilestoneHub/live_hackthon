// Client-side media helpers for the 浪 LIVE Editor.
//
// Pure module: the DOM (`document` / `URL.createObjectURL`) is only touched
// lazily inside the function body at call time, never at module top-level, so
// importing this into a 'use client' component tree stays SSR-safe. It is only
// ever invoked from browser event handlers.

/** Read an MP4's duration (ms) client-side via a hidden <video>; undefined on failure.
 *
 * A timeout is MANDATORY: for a multi-GB MP4 whose `moov` metadata atom is at the
 * END of the file (non-faststart), the browser cannot seek to it in a blob URL, so
 * NEITHER `loadedmetadata` NOR `error` ever fires — the Promise would hang forever
 * and block the batch flow at "連結影片時長" (so chat.csv never uploads → the whole
 * pipeline stalls). On timeout we resolve `undefined`; the caller treats a missing
 * duration as non-fatal and falls back to chat-relative timing. */
export function readVideoDurationMs(file: File, timeoutMs = 15000): Promise<number | undefined> {
  return new Promise((resolve) => {
    let settled = false;
    let url: string | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (val: number | undefined) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
      resolve(val);
    };
    try {
      url = URL.createObjectURL(file);
      const v = document.createElement('video');
      v.preload = 'metadata';
      timer = setTimeout(() => finish(undefined), timeoutMs); // large/non-faststart files never fire either event
      v.onloadedmetadata = () =>
        finish(Number.isFinite(v.duration) && v.duration > 0 ? Math.round(v.duration * 1000) : undefined);
      v.onerror = () => finish(undefined);
      v.src = url;
    } catch {
      finish(undefined);
    }
  });
}
