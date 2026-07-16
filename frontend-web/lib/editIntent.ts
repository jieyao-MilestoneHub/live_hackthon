// Edit-intent seam (frontend-only).
//
// Carries the user's per-project 剪接指令 (natural-language prompt) that drives
// the agent / edit-by-language track, and kicks off BOTH creative tracks per
// project. The prompt is stored client-side (localStorage) because this branch's
// contract has no field for it yet; the real prompt→edit-plan wiring is the
// TODO(seam) in requestDualRender.

import { createRender } from './api';
import type { RenderCreated } from '@/types';

const KEY = (projectId: string) => `edit_intent_${projectId}`;

/** Persist (or clear) the 剪接指令 for a project. Empty/blank → cleared → 指令版走預設模板。 */
export function saveEditIntent(projectId: string, instruction: string | undefined): void {
  if (typeof window === 'undefined') return;
  try {
    const v = (instruction ?? '').trim();
    if (v) window.localStorage.setItem(KEY(projectId), v);
    else window.localStorage.removeItem(KEY(projectId));
  } catch {
    /* localStorage unavailable — degrade gracefully */
  }
}

/** Read back the saved 剪接指令 for a project (undefined = 留白 → 預設模板). */
export function getEditIntent(projectId: string): string | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    return window.localStorage.getItem(KEY(projectId)) || undefined;
  } catch {
    return undefined;
  }
}

/** The two render tracks kicked off for one project. */
export interface DualRender {
  pipeline: RenderCreated;
  agent: RenderCreated;
}

/**
 * Kick off BOTH creative tracks for one project, in parallel:
 *   - route 'pipeline' = 模板版 (deterministic, rule-based)
 *   - route 'agent'    = 指令版 (edit-by-language)
 *
 * TODO(seam): the agent version is currently a plain `route='agent'` render; the
 * user's saved instruction (getEditIntent) is displayed in the UI but not yet
 * fed to the planner. When edit-by-language lands in the contract + api client,
 * replace the agent call with `editByLanguage(projectId, { instruction, timeline_version })`
 * — the UI, labels, and dual-track layout do not change.
 */
export async function requestDualRender(
  projectId: string,
  timelineVersion?: number,
): Promise<DualRender> {
  const [pipeline, agent] = await Promise.all([
    createRender(projectId, timelineVersion, 'pipeline'),
    createRender(projectId, timelineVersion, 'agent'),
  ]);
  return { pipeline, agent };
}
