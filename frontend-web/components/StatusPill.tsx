import type { ProjectState } from '@/types';

/** Live-status pill; magenta pulse while processing, green when ready. */
export default function StatusPill({ status }: { status: ProjectState }) {
  const variant =
    status === 'READY_TO_EDIT' || status === 'ARTIFACT_READY'
      ? 'done'
      : status === 'FAILED'
        ? 'bad'
        : 'live';
  return (
    <span className={`pill pill--${variant}`}>
      <span className="pill__dot" />
      {status}
    </span>
  );
}
