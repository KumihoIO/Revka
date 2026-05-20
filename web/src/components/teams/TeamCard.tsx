import { useMemo } from 'react';
import { Eye, Power, Pencil, Trash2 } from 'lucide-react';
import type { TeamDefinition } from '@/types/api';
import { EDGE_TYPES, getEdgeLabel, getEdgeBadgeColors, getRoleColor } from './graphHelpers';
import AgentAvatar from '@/construct/components/ui/AgentAvatar';

export default function TeamCard({
  team,
  onView,
  onToggle,
  onEdit,
  onDelete,
}: {
  team: TeamDefinition;
  onView: () => void;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const isActive = !team.deprecated;

  // Count edges by type
  const edgeCounts = useMemo(() => {
    const counts: Record<string, number> = { REPORTS_TO: 0, SUPPORTS: 0, DEPENDS_ON: 0 };
    for (const e of team.edges) {
      counts[e.edge_type] = (counts[e.edge_type] ?? 0) + 1;
    }
    return counts;
  }, [team.edges]);

  return (
    <div
      className="card p-5 animate-slide-in-up flex flex-col gap-3"
      style={{
        opacity: isActive ? 1 : 0.5,
        filter: isActive ? 'none' : 'saturate(0.3)',
        transition: 'opacity 0.3s ease, filter 0.3s ease',
      }}
    >
      {/* Top: Name + Status */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <AgentAvatar src={team.avatar_url} alt={team.name} size={34} radius={9} kind="team" />
            <h3 className="text-base font-bold truncate" style={{ color: 'var(--pc-text-primary)' }}>
              {team.name}
            </h3>
          </div>
        </div>
        <span
          className={`badge flex-shrink-0 ${isActive ? 'badge-success' : ''}`}
          style={isActive ? {} : {
            background: 'rgba(82, 82, 91, 0.15)',
            color: 'var(--pc-text-faint)',
            borderColor: 'var(--pc-border)',
          }}
        >
          <span
            className="status-dot"
            style={{
              background: isActive ? 'var(--color-status-success)' : 'var(--pc-text-faint)',
              boxShadow: isActive ? '0 0 6px var(--color-status-success)' : 'none',
            }}
          />
          {isActive ? 'Active' : 'Disabled'}
        </span>
      </div>

      {/* Description */}
      {team.description && (
        <p className="text-sm leading-relaxed" style={{ color: 'var(--pc-text-secondary)' }}>
          {team.description}
        </p>
      )}

      {/* Members */}
      <div className="flex items-center gap-2">
        <div className="flex -space-x-2">
          {team.members.length > 0
            ? team.members.slice(0, 5).map((m) => {
                const initials = m.name
                  .split(/[-_ ]/)
                  .map((w) => w[0]?.toUpperCase() ?? '')
                  .slice(0, 2)
                  .join('');
                const bg = getRoleColor(m.role);
                return (
                  <div
                    key={m.kref}
                    className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold border-2"
                    style={{
                      background: bg + '22',
                      color: bg,
                      borderColor: 'var(--pc-bg-surface)',
                    }}
                    title={m.name}
                  >
                    {initials}
                  </div>
                );
              })
            : (team.member_names ?? []).slice(0, 5).map((name, i) => {
                const initials = name
                  .split(/[-_ ]/)
                  .map((w) => w[0]?.toUpperCase() ?? '')
                  .slice(0, 2)
                  .join('');
                return (
                  <div
                    key={name + i}
                    className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold border-2"
                    style={{
                      background: 'var(--pc-accent)' + '22',
                      color: 'var(--pc-accent)',
                      borderColor: 'var(--pc-bg-surface)',
                    }}
                    title={name}
                  >
                    {initials}
                  </div>
                );
              })}
          {(team.members.length > 5 || ((team.member_count ?? 0) > 5 && team.members.length === 0)) && (
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold border-2"
              style={{
                background: 'var(--pc-hover)',
                color: 'var(--pc-text-muted)',
                borderColor: 'var(--pc-bg-surface)',
              }}
            >
              +{(team.members.length > 0 ? team.members.length : (team.member_count ?? 0)) - 5}
            </div>
          )}
        </div>
        <span className="text-xs font-medium" style={{ color: 'var(--pc-text-muted)' }}>
          {team.members.length > 0 ? team.members.length : (team.member_count ?? 0)} member{(team.members.length > 0 ? team.members.length : (team.member_count ?? 0)) !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Edge type badges */}
      {team.edges.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {EDGE_TYPES.map((type) => {
            const count = edgeCounts[type];
            if (!count) return null;
            const colors = getEdgeBadgeColors(type);
            return (
              <span
                key={type}
                className="px-2 py-0.5 rounded-md text-[11px] font-medium"
                style={{ background: colors.bg, color: colors.text }}
              >
                {getEdgeLabel(type)} ({count})
              </span>
            );
          })}
        </div>
      )}

      {/* Bottom: Actions */}
      <div className="flex items-center justify-between mt-auto pt-2 border-t" style={{ borderColor: 'var(--pc-border)' }}>
        <span className="text-xs font-medium" style={{ color: 'var(--pc-text-muted)' }}>
          {team.edges.length > 0 ? team.edges.length : (team.edge_count ?? 0)} edge{(team.edges.length > 0 ? team.edges.length : (team.edge_count ?? 0)) !== 1 ? 's' : ''}
        </span>
        <div className="flex items-center gap-1">
          <button
            className="btn-icon"
            onClick={onView}
            title="View team graph"
            aria-label="View team graph"
          >
            <Eye className="h-4 w-4" />
          </button>
          <button
            className="btn-icon"
            onClick={onToggle}
            title={isActive ? 'Disable team' : 'Enable team'}
            aria-label={isActive ? 'Disable team' : 'Enable team'}
          >
            <Power className="h-4 w-4" style={{ color: isActive ? 'var(--color-status-success)' : 'var(--pc-text-faint)' }} />
          </button>
          <button
            className="btn-icon"
            onClick={onEdit}
            title="Edit team"
            aria-label="Edit team"
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            className="btn-icon"
            onClick={onDelete}
            title="Delete team"
            aria-label="Delete team"
            style={{ color: 'var(--pc-text-muted)' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = '#f87171'; e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--pc-text-muted)'; e.currentTarget.style.background = 'transparent'; }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
