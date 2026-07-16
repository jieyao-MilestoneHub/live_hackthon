// Friendly zh-TW narration for a render's current stage, so each per-track lane
// on the batch dashboard reads as plain language rather than an enum code.
// Pure client-side — no backend needed (complements the AI progress feed).

import type { RenderState } from '@/types';

// Keys cover both the RenderState enum and the free-text stage labels the
// render worker writes ("RenderClip" / "ValidateArtifact" / "PublishArtifact" /
// "Done") — see backend-api/workers/render_worker.py.
const STAGE_NARRATION: Record<string, string> = {
  CREATED: '建立渲染工作…',
  PLANNING_SUBTITLES: '規劃雙層字幕與爆點關鍵字…',
  PLANNING_EFFECTS: '配置轉場與強調特效…',
  QUEUED: '排入影片編碼佇列…',
  RENDERING: 'FFmpeg 合成畫面、字幕與特效…',
  VALIDATING: '驗證輸出短片時長與完整性…',
  PUBLISHING: '封裝成品與縮圖…',
  SUCCEEDED: '完成，可預覽與下載。',
  FAILED: '渲染失敗。',
  RenderClip: 'FFmpeg 合成畫面、字幕與特效…',
  ValidateArtifact: '驗證輸出短片時長與完整性…',
  PublishArtifact: '封裝成品與縮圖…',
  Done: '完成，可預覽與下載。',
  Created: '建立渲染工作…',
};

export function renderStageNarration(status?: RenderState, currentStage?: string | null): string {
  if (currentStage && STAGE_NARRATION[currentStage]) return STAGE_NARRATION[currentStage];
  if (status && STAGE_NARRATION[status]) return STAGE_NARRATION[status];
  return '處理中…';
}
