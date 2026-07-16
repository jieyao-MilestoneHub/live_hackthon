'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  ApiError,
  getDownloadUrl,
  getEditPlan,
  getProject,
  getRender,
  listArtifacts,
} from '@/lib/api';
import { getEditIntent, requestDualRender, type DualRender } from '@/lib/editIntent';
import { renderStageNarration } from '@/lib/renderStage';
import { moderationAllowsPublish } from '@/lib/format';
import { POLLABLE_PROJECT_STATES, RENDER_TERMINAL_STATES } from '@/types';
import type { Artifact, EditPlan, Project, Render } from '@/types';
import StageRail from '@/components/StageRail';
import StatusPill from '@/components/StatusPill';
import ProgressFeed from '@/components/ProgressFeed';
import TrackSummaryCard from '@/components/TrackSummaryCard';

const POLL_MS = 2000;

function isPollable(status: Project['status']): boolean {
  return POLLABLE_PROJECT_STATES.has(status);
}

/** One video's run: analysis progress + dual-track (模板版 / 指令版) result. */
function ProjectRunCard({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [dual, setDual] = useState<DualRender | null>(null);
  const [pRender, setPRender] = useState<Render | null>(null);
  const [aRender, setARender] = useState<Render | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [plans, setPlans] = useState<Record<string, EditPlan | null>>({});
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadErr, setDownloadErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const triggered = useRef(false);

  const instruction = getEditIntent(projectId);

  // Initial project fetch.
  useEffect(() => {
    let alive = true;
    getProject(projectId)
      .then((p) => alive && setProject(p))
      .catch((err) => console.error(err));
    return () => {
      alive = false;
    };
  }, [projectId]);

  // Poll project status while backend work is in flight.
  useEffect(() => {
    if (!project || !isPollable(project.status)) return;
    const t = setTimeout(async () => {
      try {
        setProject(await getProject(projectId));
      } catch (err) {
        console.error(err);
        setNonce((n) => n + 1);
      }
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [project, projectId, nonce]);

  // Once the project is editable, kick off BOTH tracks (once). If it's already
  // ARTIFACT_READY (e.g. a reload), skip straight to loading the artifacts.
  useEffect(() => {
    if (!project || triggered.current) return;
    if (project.status === 'ARTIFACT_READY') {
      triggered.current = true;
      return;
    }
    if (project.status !== 'READY_TO_EDIT') return; // canonical hand-off to render
    triggered.current = true;
    requestDualRender(projectId, project.latest_timeline_version)
      .then((d) => {
        setDual(d);
        setPRender({ render_id: d.pipeline.render_id, project_id: projectId, status: d.pipeline.status, route: 'pipeline' });
        setARender({ render_id: d.agent.render_id, project_id: projectId, status: d.agent.status, route: 'agent' });
        // Resume project polling: READY_TO_EDIT isn't pollable, so bump the
        // status optimistically so the poll walks on to ARTIFACT_READY.
        setProject((p) => (p ? { ...p, status: 'RENDER_REQUESTED' } : p));
      })
      .catch((err) => console.error(err));
  }, [project, projectId]);

  // Poll each track's render until terminal.
  useEffect(() => {
    if (!dual || !pRender || RENDER_TERMINAL_STATES.has(pRender.status)) return;
    const t = setTimeout(async () => {
      try {
        setPRender(await getRender(pRender.render_id));
      } catch (err) {
        console.error(err);
        setNonce((n) => n + 1);
      }
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [dual, pRender, nonce]);

  useEffect(() => {
    if (!dual || !aRender || RENDER_TERMINAL_STATES.has(aRender.status)) return;
    const t = setTimeout(async () => {
      try {
        setARender(await getRender(aRender.render_id));
      } catch (err) {
        console.error(err);
        setNonce((n) => n + 1);
      }
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [dual, aRender, nonce]);

  // When the project is done, load both artifacts + read back each track's plan.
  useEffect(() => {
    if (project?.status !== 'ARTIFACT_READY' || artifacts.length) return;
    let alive = true;
    listArtifacts(projectId)
      .then(async (arts) => {
        if (!alive) return;
        setArtifacts(arts);
        const entries = await Promise.all(
          arts.map(async (art) => [art.render_id, await getEditPlan(projectId, art.render_id)] as const),
        );
        if (alive) setPlans(Object.fromEntries(entries));
      })
      .catch((err) => console.error(err));
    return () => {
      alive = false;
    };
  }, [project?.status, projectId, artifacts.length]);

  async function handleDownload(artifactId: string) {
    setDownloadErr(null);
    setDownloading(artifactId);
    try {
      const { url } = await getDownloadUrl(artifactId);
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setDownloadErr(
        status === 403
          ? '內容審核未通過，需管理員複核放行後才能下載。'
          : status === 401
            ? '登入已過期，請重新登入後再試。'
            : '取得下載連結失敗，請重試。',
      );
    } finally {
      setDownloading(null);
    }
  }

  const publishGated = !!project?.moderation_status && !moderationAllowsPublish(project.moderation_status);
  const feedActive = !!project && project.status !== 'ARTIFACT_READY' && project.status !== 'FAILED';
  const pipelineArt = artifacts.find((a) => (a.route ?? 'pipeline') === 'pipeline');
  const agentArt = artifacts.find((a) => a.route === 'agent');

  return (
    <div className="panel runcard reveal">
      <div className="runcard__head">
        <div>
          <div className="mono muted" style={{ fontSize: 11, letterSpacing: '0.12em' }}>
            {projectId}
          </div>
          <h2 className="cjk runcard__title">{project?.title || '影片專案'}</h2>
        </div>
        <div className="runcard__head-right">
          {project && <StatusPill status={project.status} />}
          <Link href={`/projects?id=${encodeURIComponent(projectId)}`} className="linkbtn">
            進階微調 ▸
          </Link>
        </div>
      </div>

      {project ? (
        <>
          <StageRail status={project.status} />
          <ProgressFeed projectId={projectId} active={feedActive} />

          {project.status === 'FAILED' ? (
            <p className="error">處理失敗。{project.error_code} {project.error_message}</p>
          ) : (
            <div className="fork">
              <TrackSummaryCard
                route="pipeline"
                artifact={pipelineArt}
                plan={pipelineArt ? plans[pipelineArt.render_id] : undefined}
                stageLine={renderStageNarration(pRender?.status, pRender?.current_stage)}
                publishGated={publishGated}
                downloading={downloading}
                onDownload={handleDownload}
              />
              <TrackSummaryCard
                route="agent"
                instruction={instruction}
                artifact={agentArt}
                plan={agentArt ? plans[agentArt.render_id] : undefined}
                stageLine={renderStageNarration(aRender?.status, aRender?.current_stage)}
                publishGated={publishGated}
                downloading={downloading}
                onDownload={handleDownload}
              />
            </div>
          )}

          {publishGated && <p className="hint">內容審核未通過，下載已鎖定，需管理員複核放行。</p>}
          {downloadErr && <p className="error">{downloadErr}</p>}
        </>
      ) : (
        <p className="hint">載入中…</p>
      )}
    </div>
  );
}

function BatchView() {
  const params = useSearchParams();
  const ids = (params.get('ids') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  if (!ids.length) {
    return (
      <main className="shell page">
        <div className="panel">
          <h1 className="cjk" style={{ fontSize: 24 }}>沒有批次項目</h1>
          <p className="hint">
            網址缺少 <span className="mono">?ids=</span> 參數。
          </p>
          <div style={{ marginTop: 16 }}>
            <Link href="/" className="btn btn--ghost btn--sm">← 回上傳</Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="shell page">
      <div className="panel">
        <div className="panel__head">
          <span className="panel__title cjk">批次處理 · {ids.length} 支影片</span>
          <span className="panel__eyebrow">DUAL-TRACK RUN</span>
        </div>
        <p className="hint" style={{ marginTop: 0 }}>
          每支影片一次分析、產出<span className="grad"> 模板版 </span>與<span className="grad"> 指令版 </span>兩個成品。每一步都有 AI 進度旁白。
        </p>
      </div>

      <div className="runlist">
        {ids.map((id) => (
          <ProjectRunCard key={id} projectId={id} />
        ))}
      </div>

      <div style={{ marginTop: 24 }}>
        <Link href="/" className="btn btn--ghost btn--sm">← 再上傳一批</Link>
      </div>
    </main>
  );
}

export default function BatchPage() {
  return (
    <Suspense fallback={<main className="shell page"><p className="hint">載入中…</p></main>}>
      <BatchView />
    </Suspense>
  );
}
