// Local mock data so the UI renders in dev when the backend is unreachable.
// Shape copied from contracts/samples/highlights.sample.json (highlights.v1),
// flattened into the openapi Clip / JobStatus shapes. Do NOT import from
// outside frontend-web at build time — this file is the self-contained copy.

import type { Clip, JobStatus } from '@/types';

export const MOCK_CLIPS: Clip[] = [
  {
    clip_id: 'clip_001',
    start_sec: 150.0,
    end_sec: 188.0,
    score: 0.93,
    reason: '情緒詞與驚呼密集（來了、超級厲害、成功了、太爽了），語速高峰',
    title: '衝了！成功時刻',
    download_ready: true,
  },
  {
    clip_id: 'clip_002',
    start_sec: 43.0,
    end_sec: 78.0,
    score: 0.88,
    reason: '驚嘆與高強度情緒（太扯了、太神了、起雞皮疙瘩、最精彩）',
    title: '神操作',
    download_ready: true,
  },
];

/** A fully-succeeded job with mock highlights, for offline/dev rendering. */
export function mockJobStatus(jobId: string): JobStatus {
  return {
    job_id: jobId,
    status: 'SUCCEEDED',
    current_stage: 'FINALIZING',
    progress: 100,
    highlights: MOCK_CLIPS,
  };
}
