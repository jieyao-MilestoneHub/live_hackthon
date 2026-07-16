// Client-side media helpers (browser-only).

/** Read an MP4's duration (ms) client-side via a hidden <video>; undefined on failure. */
export function readVideoDurationMs(file: File): Promise<number | undefined> {
  return new Promise((resolve) => {
    try {
      const url = URL.createObjectURL(file);
      const v = document.createElement('video');
      v.preload = 'metadata';
      v.onloadedmetadata = () => {
        URL.revokeObjectURL(url);
        resolve(Number.isFinite(v.duration) && v.duration > 0 ? Math.round(v.duration * 1000) : undefined);
      };
      v.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(undefined);
      };
      v.src = url;
    } catch {
      resolve(undefined);
    }
  });
}
