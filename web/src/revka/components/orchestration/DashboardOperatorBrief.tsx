export type DashboardOperatorBriefTone = 'steady' | 'live' | 'warn' | 'danger';

interface DashboardOperatorBriefProps {
  avatarUrl?: string | null;
  message: string;
  speaker: string;
  tone?: DashboardOperatorBriefTone;
}

export default function DashboardOperatorBrief({
  avatarUrl,
  message,
  speaker,
  tone = 'steady',
}: DashboardOperatorBriefProps) {
  return (
    <aside className="revka-dashboard-dialogue" data-tone={tone} aria-live="polite">
      <div className="revka-dashboard-dialogue-portrait" aria-hidden={!avatarUrl}>
        {avatarUrl ? (
          <img src={avatarUrl} alt="" draggable={false} />
        ) : (
          <span>OP</span>
        )}
      </div>
      <div className="revka-dashboard-dialogue-box">
        <div className="revka-dashboard-dialogue-nameplate">{speaker}</div>
        <p>
          <span aria-hidden="true" className="revka-dashboard-dialogue-mark">“</span>
          {message}
        </p>
        <span aria-hidden="true" className="revka-dashboard-dialogue-cue" />
      </div>
    </aside>
  );
}
