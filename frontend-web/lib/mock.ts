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
  DownloadUrl,
  Highlight,
  HighlightList,
  Project,
  ProjectState,
  Render,
  RenderCreated,
  RenderState,
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
