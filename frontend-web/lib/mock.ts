// Local mock data so the UI renders in dev when the backend is unreachable
// (or returns 501 for not-yet-built endpoints). Shapes mirror
// contracts/openapi.yaml v0.3.0 and the ../contracts/samples/*.sample.json
// (project-123 narrative). All times are milliseconds (ms).
//
// The offline mock walks a full, plausible path so the whole flow demos without
// a backend: upload → ANALYZING → COMPOSING → READY_TO_EDIT → (render) →
// RENDER_REQUESTED → RENDERING → ARTIFACT_READY, including a mock render + a
// downloadable sample artifact.

import type {
  Artifact,
  DownloadUrl,
  EditPlan,
  EffectItem,
  Highlight,
  HighlightList,
  Project,
  ProgressEvent,
  ProgressView,
  ProjectState,
  Render,
  RenderCreated,
  RenderState,
  Route,
  SubtitleCue,
  Timeline,
  UploadSession,
} from '@/types';

/** From contracts/samples/highlights.sample.json (highlights.v1, ms). */
export const MOCK_HIGHLIGHTS: Highlight[] = [
  {
    highlight_id: 'hl-001',
    start_ms: 150000,
    end_ms: 188000,
    score: 0.93,
    reason: '情緒詞與驚呼密集（來了、超級厲害、成功了、太爽了），語速高峰',
    transcript:
      '欸欸欸來了來了！就是現在！大家快看，這個超級厲害，我要衝了啊啊啊！成功了！我們做到了！太爽了吧這個，感謝大家的應援！',
    suggested_title: '衝了！成功時刻',
    selected: true,
    locked: false,
    // Rich explainability fields (為何入選) — normally filled by the chat-analysis
    // backend; mocked so the HighlightWhy panel demos offline.
    signal: 'fusion',
    status: 'included',
    chat_window: { start_ms: 152000, end_ms: 190000 },
    correction: { applied: true, offset_ms: -2000, note: '彈幕反應較畫面延後，事件窗往前修正 2s' },
    emotion: {
      score: 0.93,
      breakdown: { keyword: 0.52, emoji: 0.24, punctuation: 0.09, volume: 0.08 },
      counts: { 笑: 12, 驚: 8 },
    },
    detection: { minute_volume: 214, baseline_mean: 62, baseline_sigma: 26, threshold: 114 },
  },
  {
    highlight_id: 'hl-002',
    start_ms: 43000,
    end_ms: 78000,
    score: 0.88,
    reason: '驚嘆與高強度情緒（太扯了、太神了、起雞皮疙瘩、最精彩）',
    transcript:
      '哇這個真的太扯了！各位有看到嗎？這波操作也太神了吧，我完全沒想到會這樣！天啊我起雞皮疙瘩了，這絕對是今天最精彩的一段，太誇張了！',
    suggested_title: '神操作',
    selected: true,
    locked: false,
    signal: 'chat_volume',
    status: 'included',
    chat_window: { start_ms: 44000, end_ms: 79000 },
    correction: { applied: false, offset_ms: 0 },
    emotion: {
      score: 0.88,
      breakdown: { keyword: 0.44, emoji: 0.3, punctuation: 0.1, volume: 0.04 },
      counts: { 驚: 15, 讚: 6 },
    },
    detection: { minute_volume: 178, baseline_mean: 62, baseline_sigma: 26, threshold: 114 },
  },
];

const MOCK_SOURCE_DURATION_MS = 240000;

export function mockProject(projectId: string, status: ProjectState): Project {
  const editable =
    status === 'READY_TO_EDIT' ||
    status === 'RENDER_REQUESTED' ||
    status === 'RENDERING' ||
    status === 'ARTIFACT_READY';
  const rendering =
    status === 'RENDER_REQUESTED' || status === 'RENDERING' || status === 'ARTIFACT_READY';
  return {
    project_id: projectId,
    status,
    title: '（示範）我的直播精華',
    target_duration_ms: 30000,
    source_duration_ms: editable ? MOCK_SOURCE_DURATION_MS : undefined,
    source_key: `tenant=demo/project=${projectId}/source/source.mp4`,
    latest_timeline_version: editable ? 1 : undefined,
    latest_render_id: rendering ? getMockRenderId(projectId) : undefined,
    latest_artifact_id: status === 'ARTIFACT_READY' ? `mock_artifact_${projectId}` : undefined,
    created_at: '2026-07-14T10:00:00Z',
    updated_at: '2026-07-14T10:06:00Z',
  };
}

export function mockHighlightList(projectId: string): HighlightList {
  return {
    project_id: projectId,
    source_duration_ms: MOCK_SOURCE_DURATION_MS,
    highlights: MOCK_HIGHLIGHTS,
  };
}

/** From contracts/samples/timeline.sample.json (timeline.v1, ms). */
export function mockTimeline(projectId: string): Timeline {
  return {
    schema_version: 'timeline.v1',
    project_id: projectId,
    version: 1,
    target_duration_ms: 30000,
    actual_duration_ms: 29800,
    aspect_ratio: '9:16',
    subtitle_settings: { enabled: true, mode: 'auto' },
    effect_settings: { enabled: true, intensity: 'medium' },
    clips: [
      {
        timeline_order: 1,
        highlight_id: 'hl-001',
        source_start_ms: 150000,
        source_end_ms: 165000,
        timeline_start_ms: 0,
        timeline_end_ms: 15000,
      },
      {
        timeline_order: 2,
        highlight_id: 'hl-002',
        source_start_ms: 43000,
        source_end_ms: 57800,
        timeline_start_ms: 15000,
        timeline_end_ms: 29800,
      },
    ],
  };
}

export function mockUploadSession(key: string): UploadSession {
  return {
    upload_id: `mock_upload_${key}`,
    bucket: 'video-editor-raw-dev',
    key,
    parts: [{ part_number: 1, url: 'https://mock.local/upload/part-1' }],
    expires_in_sec: 900,
  };
}

// --- Simulated analysis + render progression (offline/mock only) ---------
// After a mock upload completes we stamp a start time; getProject then walks
// ANALYZING → COMPOSING → READY_TO_EDIT. After a mock render is requested we
// stamp a render start; getProject then walks RENDER_REQUESTED → RENDERING →
// ARTIFACT_READY, and getRender walks the render's own stage machine.

const analysisKey = (projectId: string) => `mock_analysis_start_${projectId}`;
const renderStartKey = (projectId: string) => `mock_render_start_${projectId}`;
const renderIdKey = (projectId: string) => `mock_render_id_${projectId}`;
const renderStartByIdKey = (renderId: string) => `mock_render_startid_${renderId}`;
const renderProjectKey = (renderId: string) => `mock_render_project_${renderId}`;

function readNum(key: string): number {
  if (typeof window === 'undefined') return 0;
  try {
    return Number(window.sessionStorage.getItem(key) || 0);
  } catch {
    return 0;
  }
}

function writeStr(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* sessionStorage unavailable — degrade gracefully */
  }
}

export function markMockAnalysisStart(projectId: string): void {
  writeStr(analysisKey(projectId), String(Date.now()));
}

function getMockRenderId(projectId: string): string | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    return window.sessionStorage.getItem(renderIdKey(projectId)) || undefined;
  } catch {
    return undefined;
  }
}

export function mockProjectStatusFor(projectId: string): ProjectState {
  if (typeof window === 'undefined') return 'READY_TO_EDIT';

  // A requested render overrides the analysis phase.
  const renderStart = readNum(renderStartKey(projectId));
  if (renderStart) {
    const elapsed = Date.now() - renderStart;
    if (elapsed < 1500) return 'RENDER_REQUESTED';
    if (elapsed < 4500) return 'RENDERING';
    return 'ARTIFACT_READY';
  }

  const start = readNum(analysisKey(projectId));
  if (!start) return 'READY_TO_EDIT';
  const elapsed = Date.now() - start;
  if (elapsed < 2500) return 'ANALYZING';
  if (elapsed < 5000) return 'COMPOSING';
  return 'READY_TO_EDIT';
}

/** Stamp a mock render start and return the 202-style RenderCreated payload. */
export function mockCreateRender(projectId: string): RenderCreated {
  const renderId = `mock_render_${Date.now()}`;
  const now = String(Date.now());
  writeStr(renderStartKey(projectId), now);
  writeStr(renderIdKey(projectId), renderId);
  writeStr(renderStartByIdKey(renderId), now);
  writeStr(renderProjectKey(renderId), projectId);
  return { render_id: renderId, status: 'CREATED' };
}

const RENDER_STAGES: { until: number; status: RenderState; stage: string }[] = [
  { until: 1200, status: 'PLANNING_SUBTITLES', stage: '規劃字幕' },
  { until: 2400, status: 'PLANNING_EFFECTS', stage: '規劃特效' },
  { until: 3200, status: 'QUEUED', stage: '排入渲染佇列' },
  { until: 4500, status: 'RENDERING', stage: '合成影片' },
];

/** Walk a mock render through its stages toward SUCCEEDED (with an artifact_id). */
export function mockRender(renderId: string): Render {
  let start = readNum(renderStartByIdKey(renderId));
  let projectId = 'mock';
  if (typeof window !== 'undefined') {
    try {
      projectId = window.sessionStorage.getItem(renderProjectKey(renderId)) || projectId;
    } catch {
      /* ignore */
    }
  }
  // Unknown render (e.g. a real render_id while offline) — start the clock now
  // so it still walks to SUCCEEDED instead of hanging.
  if (!start) {
    start = Date.now();
    writeStr(renderStartByIdKey(renderId), String(start));
  }

  const elapsed = Date.now() - start;
  const phase = RENDER_STAGES.find((s) => elapsed < s.until);
  return {
    render_id: renderId,
    project_id: projectId,
    status: phase ? phase.status : 'SUCCEEDED',
    current_stage: phase ? phase.stage : '完成',
    timeline_version: 1,
    artifact_id: phase ? undefined : `mock_artifact_${projectId}`,
  };
}

/** A publicly hosted short sample clip so the offline "download" opens a real video. */
const SAMPLE_ARTIFACT_URL =
  'https://storage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4';

export function mockDownloadUrl(_artifactId: string): DownloadUrl {
  return { url: SAMPLE_ARTIFACT_URL, expires_in_sec: 900 };
}

/** Two finished artifacts (one per route) so the dual-download UI renders offline. */
export function mockListArtifacts(projectId: string): Artifact[] {
  const routes: Route[] = ['pipeline', 'agent'];
  return routes.map((route) => ({
    artifact_id: `mock_artifact_${route}_${projectId}`,
    project_id: projectId,
    render_id: `mock_render_${route}_${projectId}`,
    route,
    timeline_version: 1,
    status: 'READY',
    duration_ms: 29800,
    aspect_ratio: '9:16',
    resolution: { width: 1080, height: 1920 },
    size_bytes: route === 'agent' ? 9_800_000 : 8_600_000,
    created_at: '2026-07-14T10:06:00Z',
  }));
}

// --- Mock progress-narration feed (AI 即時進度旁白, offline demo) ---------
// Synthesizes a stepping progress.v1 feed from the same sessionStorage
// timestamps the analysis/render walk uses, so the ProgressFeed animates
// step-by-step offline. Real narration comes from the backend /progress route.

const PROGRESS_ANALYSIS: { at: number; step: string; message: string }[] = [
  { at: 0, step: 'UPLOAD_RECEIVED', message: '已接收影片與聊天室 LOG，準備進場分析。' },
  { at: 400, step: 'VALIDATING', message: '正在驗證來源影片編碼與時間基準。' },
  { at: 1000, step: 'ANALYZING_CHATLOG', message: '正從聊天室 LOG 解析情緒起伏與洗版熱區。' },
  { at: 1800, step: 'DETECTING_HIGHLIGHTS', message: '交叉逐字稿與聊天室反應鎖定情緒高峰——已抓出 2 段。' },
  { at: 2400, step: 'MODERATION_SCAN', message: '正並行掃描畫面與字幕內容的合規風險。' },
  { at: 3000, step: 'MODERATION_DECISION', message: '彙整視覺與文字風險，判定發布分級——通過。' },
  { at: 3600, step: 'COMPOSING', message: '依起承轉合把 2 段高光編排成初剪時間軸。' },
  { at: 4600, step: 'READY', message: '初剪完成，2 段精華已可預覽微調。' },
];

const PROGRESS_RENDER: { at: number; step: string; message: string }[] = [
  { at: 0, step: 'PLANNING_SUBTITLES', message: '正逐字生成雙層字幕與爆點關鍵字動畫。' },
  { at: 1200, step: 'PLANNING_EFFECTS', message: '為爆點段落配置轉場與強調特效。' },
  { at: 2400, step: 'QUEUED', message: '剪輯藍圖就緒，排入影片編碼佇列。' },
  { at: 3200, step: 'RENDERING', message: 'FFmpeg 正合成畫面、字幕與特效輸出短片。' },
  { at: 4200, step: 'VALIDATING_ARTIFACT', message: '正驗證輸出短片的時長與完整性。' },
  { at: 4400, step: 'PUBLISHING', message: '封裝成品與縮圖，發佈可下載連結。' },
  { at: 4600, step: 'DONE', message: '完成，精華短片已可下載。' },
];

export function mockProgress(projectId: string): ProgressView {
  let aStart = readNum(analysisKey(projectId));
  if (!aStart) {
    aStart = Date.now();
    writeStr(analysisKey(projectId), String(aStart));
  }
  const aElapsed = Date.now() - aStart;
  const rStart = readNum(renderStartKey(projectId));
  const rElapsed = rStart ? Date.now() - rStart : -1;

  const events: ProgressEvent[] = [];
  const push = (base: number, at: number, step: string, message: string, running: boolean) =>
    events.push({
      schema_version: 'progress.v1',
      progress_id: `mock_prog_${step}_${projectId}`,
      project_id: projectId,
      step,
      status: running ? 'RUNNING' : 'DONE',
      message,
      created_at: new Date(base + at).toISOString(),
    });

  const analysisSoFar = PROGRESS_ANALYSIS.filter((s) => s.at <= aElapsed);
  analysisSoFar.forEach((s, i) => {
    const isLast = i === analysisSoFar.length - 1;
    const running = isLast && rElapsed < 0 && s.step !== 'READY';
    push(aStart, s.at, s.step, s.message, running);
  });

  if (rElapsed >= 0) {
    const renderSoFar = PROGRESS_RENDER.filter((s) => s.at <= rElapsed);
    renderSoFar.forEach((s, i) => {
      const isLast = i === renderSoFar.length - 1;
      push(rStart, s.at, s.step, s.message, isLast && s.step !== 'DONE');
    });
  }

  return { project_id: projectId, latest: events[events.length - 1] ?? null, events };
}

// --- Mock edit-plan readback (effects.v1 + subtitle.v1) ------------------
// Differentiates by route encoded in the render_id (…agent… vs …pipeline…),
// so the two "what this version did" cards look meaningfully different offline.

export function mockEditPlan(projectId: string, renderId: string): EditPlan {
  const isAgent = /agent/i.test(renderId);
  const effects: EffectItem[] = isAgent
    ? [
        { type: 'shake', start_ms: 1500, end_ms: 2300, strength: 0.11 },
        { type: 'zoom_in', start_ms: 8000, end_ms: 9200, strength: 0.12 },
        { type: 'shake', start_ms: 16000, end_ms: 16800, strength: 0.1 },
        { type: 'flash_transition', at_ms: 15000, duration_ms: 240 },
      ]
    : [
        { type: 'zoom_in', start_ms: 0, end_ms: 1600, strength: 0.08 },
        { type: 'zoom_in', start_ms: 15000, end_ms: 16600, strength: 0.08 },
        { type: 'flash_transition', at_ms: 15000, duration_ms: 240 },
      ];
  const kw = isAgent ? ['笑死', '爆點', '太神了'] : ['成功了', '太神了'];
  const cues: SubtitleCue[] = [
    { start_ms: 0, end_ms: 3000, text: '欸欸欸來了來了！就是現在！', kind: 'caption', emphasis_words: ['來了', '現在'] },
    {
      start_ms: 11500,
      end_ms: 15000,
      text: '成功了！',
      kind: 'keyword',
      emphasis_words: kw.slice(0, 2),
      animation: { type: 'pop', duration_ms: 260 },
    },
    {
      start_ms: 18000,
      end_ms: 21500,
      text: '太神了',
      kind: 'keyword',
      emphasis_words: [kw[2] ?? '太神了'],
      animation: { type: 'pop', duration_ms: 260 },
    },
  ];
  return {
    render_id: renderId,
    effects: {
      schema_version: 'effects.v1',
      effect_seed: isAgent ? 771020 : 834710,
      project_id: projectId,
      render_id: renderId,
      effects,
    },
    subtitle: { schema_version: 'subtitle.v1', language: 'zh-TW', project_id: projectId, render_id: renderId, cues },
  };
}
