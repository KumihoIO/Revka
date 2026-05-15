import { Pencil, Plus, Power, RefreshCw, Trash2, Users } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { AgentDefinition, TeamCreateRequest, TeamDefinition, TeamEdge, TeamUpdateRequest } from '@/types/api';
import { createTeam, deleteTeam, fetchAgents, fetchTeam, fetchTeams, toggleTeamDeprecation, updateTeam } from '@/lib/api';
import { useT } from '@/construct/hooks/useT';
import {
  SelectedMemberCard,
  TeamSummaryCard,
} from '../components/orchestration/InspectorCards';
import Panel from '../components/ui/Panel';
import Notice from '../components/ui/Notice';
import PageHeader from '../components/ui/PageHeader';
import Modal from '../components/ui/Modal';
import StateMessage from '../components/ui/StateMessage';
import TeamTopologyPanel from '../components/teams/TeamTopologyPanel';

export default function Teams() {
  const { t, tpl } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const [teams, setTeams] = useState<TeamDefinition[]>([]);
  const [selectedTeam, setSelectedTeam] = useState<TeamDefinition | null>(null);
  const [selectedMemberKref, setSelectedMemberKref] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<'summary' | 'member'>('summary');
  const [agents, setAgents] = useState<AgentDefinition[]>([]);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);

  const load = async () => {
    setLoading(true);
    return fetchTeams(true, 1, 50)
      .then((data) => {
        setTeams(data.teams);
        const requestedTeam = searchParams.get('team');
        const nextTeam = data.teams.find((team) => team.kref === requestedTeam || team.name.toLowerCase() === requestedTeam?.toLowerCase()) ?? data.teams[0] ?? null;
        if (nextTeam) {
          void fetchTeam(nextTeam.kref).then(setSelectedTeam).catch(() => {});
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchAgents(true, 1, 200)
      .then((data) => setAgents(data.agents))
      .catch(() => {});
  }, []);

  const selectedMember = useMemo(
    () => selectedTeam?.members.find((member) => member.kref === selectedMemberKref) ?? null,
    [selectedMemberKref, selectedTeam],
  );

  useEffect(() => {
    const requestedMember = searchParams.get('member');
    if (!requestedMember) {
      setSelectedMemberKref(null);
      return;
    }
    setSelectedMemberKref(requestedMember);
  }, [searchParams, selectedTeam?.kref]);

  useEffect(() => {
    if (!selectedTeam?.kref) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('team', selectedTeam.kref);
      if (selectedMemberKref) {
        next.set('member', selectedMemberKref);
      } else {
        next.delete('member');
      }
      return next;
    }, { replace: true });
  }, [selectedMemberKref, selectedTeam?.kref, setSearchParams]);

  useEffect(() => {
    setInspectorTab(selectedMemberKref ? 'member' : 'summary');
  }, [selectedTeam?.kref, selectedMemberKref]);

  const handleSaveTeam = async (values: TeamFormValues) => {
    setSaving(true);
    setError(null);
    try {
      if (selectedTeam) {
        const request: TeamUpdateRequest = {
          kref: selectedTeam.kref,
          name: values.name,
          description: values.description,
          members: values.memberKrefs,
          edges: values.edges,
        };
        const updated = await updateTeam(request);
        setSelectedTeam(updated);
        setNotice({ tone: 'success', message: tpl('teams.toast.updated', { name: updated.name }) });
      } else {
        const request: TeamCreateRequest = {
          name: values.name,
          description: values.description,
          members: values.memberKrefs,
          edges: values.edges,
        };
        const created = await createTeam(request);
        setSelectedTeam(created);
        setNotice({ tone: 'success', message: tpl('teams.toast.created', { name: created.name }) });
      }
      await load();
      setEditorOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('teams.err.save'));
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('teams.err.save') });
    } finally {
      setSaving(false);
    }
  };

  const handleToggleDeprecation = async () => {
    if (!selectedTeam) return;
    try {
      await toggleTeamDeprecation(selectedTeam.kref, !selectedTeam.deprecated);
      await load();
      const key = selectedTeam.deprecated ? 'teams.toast.reenabled' : 'teams.toast.deprecated';
      setNotice({ tone: 'success', message: tpl(key, { name: selectedTeam.name }) });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('teams.err.toggle'));
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('teams.err.toggle') });
    }
  };

  const handleDeleteTeam = async () => {
    if (!selectedTeam) return;
    setDeleting(true);
    try {
      const teamName = selectedTeam.name;
      await deleteTeam(selectedTeam.kref);
      setSelectedTeam(null);
      setSelectedMemberKref(null);
      await load();
      setNotice({ tone: 'success', message: tpl('teams.toast.deleted', { name: teamName }) });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('teams.err.delete'));
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('teams.err.delete') });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 md:h-[calc(100vh-6rem)]">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}
      <PageHeader
        kicker={t('teams.kicker')}
        title={t('teams.title')}
        actions={
          <>
            <button className="construct-button" data-variant="primary" onClick={() => { setSelectedTeam(null); setEditorOpen(true); }}>
              <Plus className="h-4 w-4" />
              {t('teams.create')}
            </button>
            <button className="construct-button" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {t('teams.refresh')}
            </button>
          </>
        }
      />

      {error ? <div className="text-sm" style={{ color: 'var(--construct-status-danger)' }}>{error}</div> : null}

      <div
        className={`grid gap-4 grid-cols-1 md:min-h-0 md:flex-1 ${
          selectedTeam
            ? 'lg:grid-cols-[20rem_minmax(0,1fr)_24rem]'
            : 'md:grid-cols-[20rem_1fr]'
        }`}
      >
        <Panel className="flex flex-col overflow-hidden p-0">
          <div className="flex items-center justify-between border-b px-3 py-2" style={{ borderColor: 'var(--construct-border-soft)' }}>
            <span className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
              {tpl('teams.count', { count: teams.length })}
            </span>
            <span className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{tpl('teams.agent_count', { count: agents.length })}</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {loading ? (
              <StateMessage compact tone="loading" title={t('teams.loading')} />
            ) : teams.length === 0 ? (
              <StateMessage compact title={t('teams.empty_title')} description={t('teams.empty_desc')} />
            ) : (
              teams.map((team) => (
                <button
                  key={team.kref}
                  type="button"
                  className="mb-1 w-full rounded-[12px] border px-3 py-3 text-left transition"
                  style={{
                    borderColor: team.kref === selectedTeam?.kref ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)',
                    background: team.kref === selectedTeam?.kref
                      ? 'color-mix(in srgb, var(--construct-signal-selected) 14%, var(--construct-bg-panel-strong))'
                      : 'transparent',
                  }}
                  onClick={() => {
                    setSelectedMemberKref(null);
                    void fetchTeam(team.kref).then(setSelectedTeam).catch((err) => setError(err.message));
                  }}
                >
                  <div className="flex items-center gap-2">
                    <Users className="h-4 w-4 shrink-0" style={{ color: 'var(--construct-signal-network)' }} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold">{team.name}</div>
                      <div className="mt-0.5 text-[11px]" style={{ color: 'var(--construct-text-secondary)' }}>
                        {tpl('teams.members_suffix', { members: team.members.length || team.member_count || 0, edges: team.edges.length || team.edge_count || 0 })}
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: team.deprecated ? 'color-mix(in srgb, var(--construct-status-danger) 12%, transparent)' : 'color-mix(in srgb, var(--construct-status-success) 12%, transparent)', color: team.deprecated ? 'var(--construct-status-danger)' : 'var(--construct-status-success)' }}>
                      {team.deprecated ? t('teams.status.off') : t('teams.status.live')}
                    </span>
                  </div>
                  {team.description ? (
                    <p className="mt-1.5 line-clamp-2 text-[11px] leading-4" style={{ color: 'var(--construct-text-faint)' }}>
                      {team.description}
                    </p>
                  ) : null}
                </button>
              ))
            )}
          </div>
        </Panel>

        <Panel className="flex flex-col overflow-y-auto p-4">
          {!selectedTeam ? (
            <div className="flex flex-1 items-center justify-center">
              <StateMessage
                tone={loading ? 'loading' : 'empty'}
                title={loading ? t('teams.loading_desc') : t('teams.none_selected_title')}
                description={t('teams.none_selected_desc')}
              />
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{selectedTeam.name}</div>
                  <span className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{
                    background: selectedTeam.deprecated ? 'color-mix(in srgb, var(--construct-status-danger) 12%, transparent)' : 'color-mix(in srgb, var(--construct-status-success) 12%, transparent)',
                    color: selectedTeam.deprecated ? 'var(--construct-status-danger)' : 'var(--construct-status-success)',
                  }}>
                    {selectedTeam.deprecated ? t('teams.status.deprecated') : t('teams.status.active')}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  <button className="construct-button" onClick={() => setEditorOpen(true)} title={t('teams.action.edit')}>
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button className="construct-button" onClick={handleToggleDeprecation} title={selectedTeam.deprecated ? t('teams.action.reenable') : t('teams.action.deprecate')}>
                    <Power className="h-3.5 w-3.5" />
                  </button>
                  <button className="construct-button" onClick={handleDeleteTeam} disabled={deleting} title={t('teams.action.delete')}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              <div className="mt-3 grid gap-3 grid-cols-3">
                <Panel className="p-3" variant="utility">
                  <div className="construct-kicker">{t('teams.stats.members')}</div>
                  <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                    {selectedTeam.members.length || selectedTeam.member_count || 0}
                  </div>
                </Panel>
                <Panel className="p-3" variant="utility">
                  <div className="construct-kicker">{t('teams.stats.edges')}</div>
                  <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                    {selectedTeam.edges.length || selectedTeam.edge_count || 0}
                  </div>
                </Panel>
                <Panel className="p-3" variant="utility">
                  <div className="construct-kicker">{t('teams.stats.status')}</div>
                  <div className="mt-1 text-sm font-semibold" style={{ color: selectedTeam.deprecated ? 'var(--construct-status-danger)' : 'var(--construct-status-success)' }}>
                    {selectedTeam.deprecated ? t('teams.status.deprecated') : t('teams.status.active')}
                  </div>
                </Panel>
              </div>

              <div className="mt-4 min-h-0 flex-1">
                <TeamTopologyPanel team={selectedTeam} onSelectMember={setSelectedMemberKref} selectedMemberKref={selectedMemberKref} />
              </div>
            </>
          )}
        </Panel>

        {selectedTeam ? (
          <Panel className="flex flex-col overflow-hidden p-0">
            <div className="flex items-center gap-1 border-b px-3 py-2" style={{ borderColor: 'var(--construct-border-soft)' }}>
              {([
                ['summary', t('teams.tab.summary')],
                ['member', t('teams.tab.member')],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className="construct-tab-button"
                  data-active={String(inspectorTab === id)}
                  aria-selected={inspectorTab === id}
                  onClick={() => setInspectorTab(id as typeof inspectorTab)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              {inspectorTab === 'summary' ? (
                <TeamSummaryCard team={selectedTeam} />
              ) : null}

              {inspectorTab === 'member' ? (
                <SelectedMemberCard
                  member={selectedMember}
                  footer={selectedMember ? (
                    <Link to="/agents" style={{ color: 'var(--construct-signal-network)' }} className="text-xs">
                      {t('teams.open_agent_registry')}
                    </Link>
                  ) : undefined}
                />
              ) : null}
            </div>
          </Panel>
        ) : null}
      </div>

      {editorOpen ? (
        <TeamEditorModal
          team={selectedTeam}
          agents={agents}
          saving={saving}
          onClose={() => setEditorOpen(false)}
          onSave={handleSaveTeam}
        />
      ) : null}
    </div>
  );
}

interface TeamFormValues {
  name: string;
  description: string;
  memberKrefs: string[];
  edges: TeamEdge[];
}

function TeamEditorModal({
  team,
  agents,
  saving,
  onClose,
  onSave,
}: {
  team: TeamDefinition | null;
  agents: AgentDefinition[];
  saving: boolean;
  onClose: () => void;
  onSave: (values: TeamFormValues) => void;
}) {
  const { t } = useT();
  const [name, setName] = useState(team?.name ?? '');
  const [description, setDescription] = useState(team?.description ?? '');
  const [memberKrefs, setMemberKrefs] = useState<string[]>(team?.members.map((member) => member.kref) ?? []);
  const [edges, setEdges] = useState<TeamEdge[]>(team?.edges ?? []);
  const [fromKref, setFromKref] = useState('');
  const [toKref, setToKref] = useState('');
  const [edgeType, setEdgeType] = useState<TeamEdge['edge_type']>('REPORTS_TO');

  useEffect(() => {
    setName(team?.name ?? '');
    setDescription(team?.description ?? '');
    setMemberKrefs(team?.members.map((member) => member.kref) ?? []);
    setEdges(team?.edges ?? []);
  }, [team]);

  useEffect(() => {
    setEdges((current) =>
      current.filter((edge) => memberKrefs.includes(edge.from_kref) && memberKrefs.includes(edge.to_kref)),
    );
    setFromKref((current) => (memberKrefs.includes(current) ? current : memberKrefs[0] ?? ''));
    setToKref((current) => (memberKrefs.includes(current) ? current : memberKrefs[1] ?? memberKrefs[0] ?? ''));
  }, [memberKrefs]);

  const selectedAgents = useMemo(
    () => agents.filter((agent) => memberKrefs.includes(agent.kref)),
    [agents, memberKrefs],
  );

  const draftTeam = useMemo<TeamDefinition>(() => ({
    kref: team?.kref ?? 'draft://team',
    name: name || t('teams.form.draft_team_name'),
    description,
    deprecated: false,
    created_at: team?.created_at ?? '',
    members: selectedAgents.map((agent) => ({
      kref: agent.kref,
      name: agent.name,
      role: agent.role,
      agent_type: agent.agent_type,
      model: agent.model,
      identity: agent.identity,
      expertise: agent.expertise,
    })),
    edges,
    member_count: selectedAgents.length,
    edge_count: edges.length,
  }), [description, edges, name, selectedAgents, team?.created_at, team?.kref, t]);

  const addEdge = () => {
    if (!fromKref || !toKref || fromKref === toKref) return;
    setEdges((current) => {
      const exists = current.some((edge) => edge.from_kref === fromKref && edge.to_kref === toKref && edge.edge_type === edgeType);
      if (exists) return current;
      return [...current, { from_kref: fromKref, to_kref: toKref, edge_type: edgeType }];
    });
  };

  return (
    <Modal
      title={team ? t('teams.modal.edit_title') : t('teams.modal.create_title')}
      description={t('teams.modal.description')}
      onClose={onClose}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('teams.form.name')}</span>
          <input className="construct-input" value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('teams.form.description')}</span>
          <input className="construct-input" value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
      </div>
      <div className="mt-4">
        <div className="construct-kicker">{t('teams.form.members')}</div>
        <div className="mt-3 grid max-h-[22rem] gap-2 overflow-auto md:grid-cols-2">
          {agents.map((agent) => {
            const checked = memberKrefs.includes(agent.kref);
            return (
              <label key={agent.kref} className="flex items-start gap-3 rounded-[12px] border p-3" style={{ borderColor: checked ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)' }}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(event) => {
                    setMemberKrefs((current) => event.target.checked ? [...current, agent.kref] : current.filter((value) => value !== agent.kref));
                  }}
                />
                <div className="min-w-0">
                  <div className="text-sm font-medium" style={{ color: 'var(--construct-text-primary)' }}>{agent.name}</div>
                  <div className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{agent.role} / {agent.agent_type}</div>
                  <div className="mt-1 line-clamp-2 text-xs" style={{ color: 'var(--construct-text-faint)' }}>{agent.identity}</div>
                </div>
              </label>
            );
          })}
        </div>
      </div>
      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-4">
          <div>
            <div className="construct-kicker">{t('teams.form.topology_preview')}</div>
            <div className="mt-3">
              <TeamTopologyPanel team={draftTeam} onSelectMember={() => {}} />
            </div>
          </div>
          <Panel className="p-4" variant="utility">
            <div className="construct-kicker">{t('teams.form.edge_authoring')}</div>
            <div className="mt-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_10rem_auto]">
              <select className="construct-input" value={fromKref} onChange={(event) => setFromKref(event.target.value)}>
                <option value="">{t('teams.form.from_member')}</option>
                {selectedAgents.map((agent) => (
                  <option key={agent.kref} value={agent.kref}>{agent.name}</option>
                ))}
              </select>
              <select className="construct-input" value={toKref} onChange={(event) => setToKref(event.target.value)}>
                <option value="">{t('teams.form.to_member')}</option>
                {selectedAgents.map((agent) => (
                  <option key={agent.kref} value={agent.kref}>{agent.name}</option>
                ))}
              </select>
              <select className="construct-input" value={edgeType} onChange={(event) => setEdgeType(event.target.value as TeamEdge['edge_type'])}>
                <option value="REPORTS_TO">{t('teams.form.edge_reports_to')}</option>
                <option value="SUPPORTS">{t('teams.form.edge_supports')}</option>
                <option value="DEPENDS_ON">{t('teams.form.edge_depends_on')}</option>
              </select>
              <button className="construct-button" onClick={addEdge} disabled={!fromKref || !toKref || fromKref === toKref}>{t('teams.form.add_edge')}</button>
            </div>
            <div className="mt-3 space-y-2">
              {edges.map((edge) => {
                const fromName = selectedAgents.find((agent) => agent.kref === edge.from_kref)?.name ?? edge.from_kref;
                const toName = selectedAgents.find((agent) => agent.kref === edge.to_kref)?.name ?? edge.to_kref;
                return (
                  <div key={`${edge.from_kref}-${edge.to_kref}-${edge.edge_type}`} className="flex items-center justify-between gap-2 rounded-[12px] border p-3 text-sm" style={{ borderColor: 'var(--construct-border-soft)' }}>
                    <div style={{ color: 'var(--construct-text-primary)' }}>
                      {fromName} <span style={{ color: 'var(--construct-text-faint)' }}>{edge.edge_type}</span> {toName}
                    </div>
                    <button
                      className="text-xs"
                      style={{ color: 'var(--construct-status-danger)' }}
                      onClick={() => setEdges((current) => current.filter((candidate) => !(candidate.from_kref === edge.from_kref && candidate.to_kref === edge.to_kref && candidate.edge_type === edge.edge_type)))}
                    >
                      {t('teams.form.remove')}
                    </button>
                  </div>
                );
              })}
              {edges.length === 0 ? (
                <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>{t('teams.form.no_edges')}</div>
              ) : null}
            </div>
          </Panel>
        </div>
        <Panel className="p-4" variant="secondary">
          <div className="construct-kicker">{t('teams.form.draft_summary')}</div>
          <div className="mt-3 space-y-2 text-sm">
            <div><span style={{ color: 'var(--construct-text-faint)' }}>{t('teams.form.draft_members')}</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{selectedAgents.length}</span></div>
            <div><span style={{ color: 'var(--construct-text-faint)' }}>{t('teams.form.draft_edges')}</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{edges.length}</span></div>
            <p style={{ color: 'var(--construct-text-secondary)' }}>
              {t('teams.form.draft_hint')}
            </p>
          </div>
        </Panel>
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button className="construct-button" onClick={onClose}>{t('teams.form.cancel')}</button>
        <button
          className="construct-button"
          data-variant="primary"
          disabled={saving || !name.trim() || memberKrefs.length === 0}
          onClick={() => onSave({ name: name.trim(), description: description.trim(), memberKrefs, edges })}
        >
          {saving ? t('teams.form.saving') : team ? t('teams.form.save') : t('teams.modal.create_title')}
        </button>
      </div>
    </Modal>
  );
}
