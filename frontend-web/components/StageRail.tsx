import type { ProjectState } from '@/types';

// The two-stage pipeline is a real sequence, so a stepper is earned structure
// (unlike the four editor panels, which aren't ordered and stay unnumbered).

const STEPS = ['上傳', '分析', '組片', '編輯', '渲染'] as const;

const STEP_INDEX: Record<ProjectState, number> = {
  CREATED: 0,
  UPLOAD_PENDING: 0,
  UPLOADING: 0,
  ANALYZING: 1,
  COMPOSING: 2,
  READY_TO_EDIT: 3,
  RENDER_REQUESTED: 4,
  RENDERING: 4,
  ARTIFACT_READY: 5, // all done
  FAILED: -1,
};

export default function StageRail({ status }: { status: ProjectState }) {
  const active = STEP_INDEX[status];
  return (
    <div className="rail" role="list" aria-label="專案流程">
      {STEPS.map((label, i) => {
        const state = i < active ? 'is-done' : i === active ? 'is-active' : '';
        return (
          <div key={label} style={{ display: 'inline-flex', alignItems: 'center' }}>
            {i > 0 && <span className="rail__line" />}
            <span className={`rail__step ${state}`} role="listitem">
              <span className="rail__node" />
              <span className="rail__label">{label}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
