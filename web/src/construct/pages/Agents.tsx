import { useEffect, useMemo, useState } from 'react';
import { Brain, Filter, ImagePlus, Pencil, Plus, Power, RefreshCw, Search, Trash2 } from 'lucide-react';
import type { AgentCreateRequest, AgentDefinition, AgentUpdateRequest } from '@/types/api';
import { createAgent, deleteAgent, fetchAgents, toggleAgentDeprecation, updateAgent, uploadAgentAvatar } from '@/lib/api';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import Modal from '../components/ui/Modal';
import Notice from '../components/ui/Notice';
import StateMessage from '../components/ui/StateMessage';
import AgentAvatar from '../components/ui/AgentAvatar';
import { formatLocalDateTime } from '../lib/datetime';
import { useT } from '@/construct/hooks/useT';

interface AgentFormValues {
  name: string;
  identity: string;
  soul: string;
  expertise: string[];
  tone: string;
  role: string;
  agent_type: string;
  model: string;
  system_hint: string;
  avatarFile?: File | null;
}

const EMPTY_FORM: AgentFormValues = {
  name: '',
  identity: '',
  soul: '',
  expertise: [],
  tone: '',
  role: 'coder',
  agent_type: 'claude',
  model: '',
  system_hint: '',
};

export default function Agents() {
  const { t, tpl } = useT();
  const [agents, setAgents] = useState<AgentDefinition[]>([]);
  const [selectedAgentKref, setSelectedAgentKref] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<'all' | 'coder' | 'reviewer' | 'researcher'>('all');
  const [showDisabled, setShowDisabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [editorMode, setEditorMode] = useState<'create' | 'edit' | null>(null);
  const [inspectorTab, setInspectorTab] = useState<'summary' | 'prompt'>('summary');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchAgents(true, 1, 200);
      setAgents(page.agents);
      setSelectedAgentKref((current) => current ?? page.agents[0]?.kref ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('agents.err.load'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filteredAgents = useMemo(() => {
    const query = search.trim().toLowerCase();
    return agents.filter((agent) => {
      if (!showDisabled && agent.deprecated) return false;
      if (roleFilter !== 'all' && agent.role !== roleFilter) return false;
      if (!query) return true;
      return (
        agent.name.toLowerCase().includes(query)
        || agent.identity.toLowerCase().includes(query)
        || agent.soul.toLowerCase().includes(query)
        || agent.expertise.some((item) => item.toLowerCase().includes(query))
      );
    });
  }, [agents, roleFilter, search, showDisabled]);

  const selectedAgent = useMemo(
    () => filteredAgents.find((agent) => agent.kref === selectedAgentKref)
      ?? agents.find((agent) => agent.kref === selectedAgentKref)
      ?? filteredAgents[0]
      ?? agents[0]
      ?? null,
    [agents, filteredAgents, selectedAgentKref],
  );

  useEffect(() => {
    if (!selectedAgent && filteredAgents.length > 0) {
      setSelectedAgentKref(filteredAgents[0]?.kref ?? null);
    }
  }, [filteredAgents, selectedAgent]);

  useEffect(() => {
    setInspectorTab('summary');
  }, [selectedAgent?.kref]);

  const activeCount = useMemo(() => agents.filter((agent) => !agent.deprecated).length, [agents]);
  const codexCount = useMemo(() => agents.filter((agent) => agent.agent_type === 'codex').length, [agents]);

  const handleSave = async (values: AgentFormValues) => {
    setSaving(true);
    try {
      const { avatarFile, ...agentValues } = values;
      if (editorMode === 'edit' && selectedAgent) {
        const request: AgentUpdateRequest = {
          ...agentValues,
          kref: selectedAgent.kref,
          model: values.model || undefined,
          system_hint: values.system_hint || undefined,
        };
        let updated = await updateAgent(request);
        if (avatarFile) {
          updated = await uploadAgentAvatar(updated.kref, avatarFile);
        }
        setAgents((current) => current.map((agent) => (agent.kref === updated.kref ? updated : agent)));
        setSelectedAgentKref(updated.kref);
        setNotice({ tone: 'success', message: tpl('agents.toast.updated', { name: updated.name }) });
      } else {
        const request: AgentCreateRequest = {
          ...agentValues,
          model: values.model || undefined,
          system_hint: values.system_hint || undefined,
        };
        let created = await createAgent(request);
        if (avatarFile) {
          created = await uploadAgentAvatar(created.kref, avatarFile);
        }
        setAgents((current) => [created, ...current]);
        setSelectedAgentKref(created.kref);
        setNotice({ tone: 'success', message: tpl('agents.toast.created', { name: created.name }) });
      }
      setEditorMode(null);
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('agents.err.save') });
    } finally {
      setSaving(false);
    }
  };

  const handleToggleDeprecation = async () => {
    if (!selectedAgent) return;
    try {
      const updated = await toggleAgentDeprecation(selectedAgent.kref, !selectedAgent.deprecated);
      setAgents((current) => current.map((agent) => (agent.kref === updated.kref ? updated : agent)));
      const key = updated.deprecated ? 'agents.toast.deprecated' : 'agents.toast.reenabled';
      setNotice({ tone: 'success', message: tpl(key, { name: updated.name }) });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('agents.err.toggle') });
    }
  };

  const handleDeleteAgent = async () => {
    if (!selectedAgent) return;
    setDeleting(true);
    try {
      const agentName = selectedAgent.name;
      await deleteAgent(selectedAgent.kref);
      setAgents((current) => current.filter((agent) => agent.kref !== selectedAgent.kref));
      setSelectedAgentKref(null);
      setNotice({ tone: 'success', message: tpl('agents.toast.deleted', { name: agentName }) });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('agents.err.delete') });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 md:h-[calc(100vh-6rem)]">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}
      <PageHeader
        kicker={t('agents.kicker')}
        title={t('agents.title')}
        actions={(
          <>
            <button className="construct-button" data-variant="primary" onClick={() => setEditorMode('create')}>
              <Plus className="h-4 w-4" />
              {t('agents.create')}
            </button>
            <button className="construct-button" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {t('agents.refresh')}
            </button>
          </>
        )}
      />

      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--construct-text-faint)' }} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="construct-input pl-10"
            placeholder={t('agents.search_placeholder')}
          />
        </div>
        <select className="construct-input w-auto" value={roleFilter} onChange={(event) => setRoleFilter(event.target.value as typeof roleFilter)}>
          <option value="all">{t('agents.filter.all_roles')}</option>
          <option value="coder">{t('agents.filter.coder')}</option>
          <option value="reviewer">{t('agents.filter.reviewer')}</option>
          <option value="researcher">{t('agents.filter.researcher')}</option>
        </select>
        <button
          type="button"
          className="construct-button"
          onClick={() => setShowDisabled((current) => !current)}
        >
          <Filter className="h-4 w-4" />
          {showDisabled ? t('agents.filter.hide_disabled') : t('agents.filter.show_disabled')}
        </button>
      </div>

      {error ? <div className="text-sm" style={{ color: 'var(--construct-status-danger)' }}>{error}</div> : null}

      <div
        className={`grid gap-4 grid-cols-1 md:min-h-0 md:flex-1 ${
          selectedAgent
            ? 'lg:grid-cols-[20rem_minmax(0,1fr)_26rem]'
            : 'md:grid-cols-[20rem_1fr]'
        }`}
      >
        <Panel className="flex flex-col overflow-hidden p-0">
          <div className="flex items-center justify-between border-b px-3 py-2" style={{ borderColor: 'var(--construct-border-soft)' }}>
            <span className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
              {tpl('agents.count', { count: filteredAgents.length })}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {loading ? (
              <StateMessage compact tone="loading" title={t('agents.loading')} />
            ) : filteredAgents.length === 0 ? (
              <StateMessage compact title={t('agents.empty_title')} description={t('agents.empty_desc')} />
            ) : (
              filteredAgents.map((agent) => (
                <button
                  key={agent.kref}
                  type="button"
                  className="mb-1 w-full rounded-[12px] border px-3 py-3 text-left transition"
                  style={{
                    opacity: agent.deprecated ? 0.68 : 1,
                    borderColor: agent.kref === selectedAgent?.kref ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)',
                    background: agent.kref === selectedAgent?.kref
                      ? 'color-mix(in srgb, var(--construct-signal-selected) 14%, var(--construct-bg-panel-strong))'
                      : 'transparent',
                  }}
                  onClick={() => setSelectedAgentKref(agent.kref)}
                >
                  <div className="flex items-center gap-2">
                    <AgentAvatar
                      src={agent.avatar_url}
                      alt={agent.name}
                      size={28}
                      radius={8}
                      style={{ opacity: agent.deprecated ? 0.72 : 1 }}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold">{agent.name}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--construct-text-secondary)' }}>
                        <span>{agent.role}</span>
                        <span style={{ color: 'var(--construct-text-faint)' }}>/</span>
                        <span>{agent.agent_type}</span>
                        {agent.model ? (
                          <>
                            <span style={{ color: 'var(--construct-text-faint)' }}>/</span>
                            <span className="truncate">{agent.model}</span>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-[11px] leading-4" style={{ color: 'var(--construct-text-faint)' }}>
                    {agent.identity}
                  </p>
                  {agent.expertise.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {agent.expertise.slice(0, 3).map((item) => (
                        <span key={item} className="rounded-full px-1.5 py-0.5 text-[9px] font-semibold" style={{ background: 'var(--construct-signal-network-soft)', color: 'var(--construct-signal-network)' }}>
                          {item}
                        </span>
                      ))}
                      {agent.expertise.length > 3 ? (
                        <span className="text-[9px] py-0.5" style={{ color: 'var(--construct-text-faint)' }}>+{agent.expertise.length - 3}</span>
                      ) : null}
                    </div>
                  ) : null}
                </button>
              ))
            )}
          </div>
        </Panel>

        <Panel className="flex flex-col overflow-y-auto p-4">
          {!selectedAgent ? (
            <div className="flex flex-1 items-center justify-center">
              <StateMessage
                tone={loading ? 'loading' : 'empty'}
                title={loading ? t('agents.loading_desc') : t('agents.none_selected_title')}
                description={t('agents.none_selected_desc')}
              />
            </div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <AgentAvatar src={selectedAgent.avatar_url} alt={selectedAgent.name} size={44} radius={12} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Brain className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
                      <div className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{selectedAgent.name}</div>
                    </div>
                    <div className="mt-1 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
                      {selectedAgent.role} / {selectedAgent.agent_type}{selectedAgent.model ? ` / ${selectedAgent.model}` : ''}
                    </div>
                  </div>
                </div>
                <span className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{
                  background: selectedAgent.deprecated ? 'color-mix(in srgb, var(--construct-status-danger) 12%, transparent)' : 'color-mix(in srgb, var(--construct-status-success) 12%, transparent)',
                  color: selectedAgent.deprecated ? 'var(--construct-status-danger)' : 'var(--construct-status-success)',
                }}>
                  {selectedAgent.deprecated ? t('agents.status.disabled') : t('agents.status.active')}
                </span>
              </div>

              <div className="mt-3 grid gap-3 grid-cols-3">
                <Panel className="p-3" variant="utility">
                  <div className="construct-kicker">{t('agents.stats.registry')}</div>
                  <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{agents.length}</div>
                </Panel>
                <Panel className="p-3" variant="utility">
                  <div className="construct-kicker">{t('agents.stats.active')}</div>
                  <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{activeCount}</div>
                </Panel>
                <Panel className="p-3" variant="utility">
                  <div className="construct-kicker">{t('agents.stats.codex')}</div>
                  <div className="mt-1 text-lg font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{codexCount}</div>
                </Panel>
              </div>

              <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
                <div className="space-y-4">
                  <div>
                    <div className="construct-kicker">{t('agents.section.identity')}</div>
                    <p className="mt-2 text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
                      {selectedAgent.identity}
                    </p>
                  </div>
                  <div>
                    <div className="construct-kicker">{t('agents.section.soul')}</div>
                    <p className="mt-2 text-sm leading-7" style={{ color: 'var(--construct-text-secondary)' }}>
                      {selectedAgent.soul || t('agents.section.soul_empty')}
                    </p>
                  </div>
                  <div>
                    <div className="construct-kicker">{t('agents.section.expertise')}</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {selectedAgent.expertise.length > 0 ? selectedAgent.expertise.map((item) => (
                        <span
                          key={item}
                          className="rounded-full px-3 py-1 text-xs font-semibold"
                          style={{ background: 'var(--construct-signal-network-soft)', color: 'var(--construct-text-primary)', border: '1px solid var(--construct-border-soft)' }}
                        >
                          {item}
                        </span>
                      )) : (
                        <span className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>{t('agents.section.no_expertise')}</span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="rounded-[16px] border p-4" style={{ borderColor: 'var(--construct-border-soft)' }}>
                  <div className="construct-kicker">{t('agents.section.voice')}</div>
                  <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                    {selectedAgent.tone || t('agents.section.voice_empty')}
                  </div>
                  <div className="mt-4 grid gap-3 text-sm">
                    <div>
                      <div style={{ color: 'var(--construct-text-faint)' }}>{t('agents.section.revision')}</div>
                      <div className="mt-1 font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{selectedAgent.revision_number ?? selectedAgent.revision ?? '--'}</div>
                    </div>
                    <div>
                      <div style={{ color: 'var(--construct-text-faint)' }}>{t('agents.section.created')}</div>
                      <div className="mt-1 font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                        {formatLocalDateTime(selectedAgent.created_at) || '--'}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-4 flex gap-2">
                <button className="construct-button" onClick={() => setEditorMode('edit')}>
                  <Pencil className="h-4 w-4" />
                  {t('agents.action.edit')}
                </button>
                <button className="construct-button" onClick={handleToggleDeprecation}>
                  <Power className="h-4 w-4" />
                  {selectedAgent.deprecated ? t('agents.action.reenable') : t('agents.action.deprecate')}
                </button>
                <button className="construct-button" onClick={handleDeleteAgent} disabled={deleting}>
                  <Trash2 className="h-4 w-4" />
                  {deleting ? t('agents.action.deleting') : t('agents.action.delete')}
                </button>
              </div>
            </>
          )}
        </Panel>

        {selectedAgent ? (
          <Panel className="flex flex-col overflow-hidden p-0">
            <div className="flex items-center gap-1 border-b px-3 py-2" style={{ borderColor: 'var(--construct-border-soft)' }}>
              {[
                ['summary', t('agents.tab.summary')],
                ['prompt', t('agents.tab.prompt')],
              ].map(([id, label]) => (
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
                <div className="space-y-3 text-sm">
                  {[
                    [t('agents.summary.name'), selectedAgent.name],
                    [t('agents.summary.role'), selectedAgent.role],
                    [t('agents.summary.type'), selectedAgent.agent_type],
                    [t('agents.summary.model'), selectedAgent.model || t('agents.summary.model_default')],
                    [t('agents.summary.tone'), selectedAgent.tone || '--'],
                    [t('agents.summary.revision'), String(selectedAgent.revision_number ?? selectedAgent.revision ?? '--')],
                    [t('agents.summary.created'), formatLocalDateTime(selectedAgent.created_at) || '--'],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-[12px] border p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
                      <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>{label}</div>
                      <div className="mt-1 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{value}</div>
                    </div>
                  ))}
                </div>
              ) : null}

              {inspectorTab === 'prompt' ? (
                <div className="space-y-4">
                  <div>
                    <div className="construct-kicker">{t('agents.prompt.system_hint')}</div>
                    <pre className="mt-2 whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--construct-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>
                      {selectedAgent.system_hint || t('agents.prompt.system_hint_empty')}
                    </pre>
                  </div>
                  <div>
                    <div className="construct-kicker">{t('agents.prompt.identity_seed')}</div>
                    <pre className="mt-2 whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--construct-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>
                      {selectedAgent.identity}
                    </pre>
                  </div>
                </div>
              ) : null}
            </div>
          </Panel>
        ) : null}
      </div>

      {editorMode ? (
        <AgentEditorModal
          mode={editorMode}
          agent={editorMode === 'edit' ? selectedAgent : null}
          saving={saving}
          onClose={() => setEditorMode(null)}
          onSave={handleSave}
        />
      ) : null}
    </div>
  );
}

function AgentEditorModal({
  mode,
  agent,
  saving,
  onClose,
  onSave,
}: {
  mode: 'create' | 'edit';
  agent: AgentDefinition | null;
  saving: boolean;
  onClose: () => void;
  onSave: (values: AgentFormValues) => void;
}) {
  const { t } = useT();
  const [name, setName] = useState(agent?.name ?? '');
  const [identity, setIdentity] = useState(agent?.identity ?? '');
  const [soul, setSoul] = useState(agent?.soul ?? '');
  const [tone, setTone] = useState(agent?.tone ?? '');
  const [role, setRole] = useState(agent?.role ?? 'coder');
  const [agentType, setAgentType] = useState(agent?.agent_type ?? 'claude');
  const [model, setModel] = useState(agent?.model ?? '');
  const [systemHint, setSystemHint] = useState(agent?.system_hint ?? '');
  const [expertiseInput, setExpertiseInput] = useState(agent?.expertise.join(', ') ?? '');
  const [avatarFile, setAvatarFile] = useState<File | null>(null);

  useEffect(() => {
    setName(agent?.name ?? EMPTY_FORM.name);
    setIdentity(agent?.identity ?? EMPTY_FORM.identity);
    setSoul(agent?.soul ?? EMPTY_FORM.soul);
    setTone(agent?.tone ?? EMPTY_FORM.tone);
    setRole(agent?.role ?? EMPTY_FORM.role);
    setAgentType(agent?.agent_type ?? EMPTY_FORM.agent_type);
    setModel(agent?.model ?? EMPTY_FORM.model);
    setSystemHint(agent?.system_hint ?? EMPTY_FORM.system_hint);
    setExpertiseInput(agent?.expertise.join(', ') ?? '');
    setAvatarFile(null);
  }, [agent]);

  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(agent?.avatar_url ?? null);

  useEffect(() => {
    if (!avatarFile) {
      setAvatarPreviewUrl(agent?.avatar_url ?? null);
      return undefined;
    }
    const url = URL.createObjectURL(avatarFile);
    setAvatarPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [agent?.avatar_url, avatarFile]);

  const canSave = name.trim() && identity.trim() && soul.trim();

  return (
    <Modal
      title={mode === 'create' ? t('agents.modal.create_title') : t('agents.modal.edit_title')}
      description={t('agents.modal.description')}
      onClose={onClose}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.name')}</span>
          <input className="construct-input" value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.tone')}</span>
          <input className="construct-input" value={tone} onChange={(event) => setTone(event.target.value)} placeholder={t('agents.form.tone_placeholder')} />
        </label>
      </div>

      <div className="mt-4 flex items-center gap-3 rounded-[12px] border p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
        <AgentAvatar src={avatarPreviewUrl} alt={name || agent?.name || 'Agent'} size={54} radius={14} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
            Profile image
          </div>
          <div className="mt-1 text-xs" style={{ color: 'var(--construct-text-faint)' }}>
            PNG, JPEG, or WebP.
          </div>
        </div>
        <label className="construct-button cursor-pointer">
          <ImagePlus className="h-4 w-4" />
          Upload
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="sr-only"
            disabled={saving}
            onChange={(event) => setAvatarFile(event.target.files?.[0] ?? null)}
          />
        </label>
      </div>

      <label className="mt-4 grid gap-2 text-sm">
        <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.identity')}</span>
        <textarea className="construct-input min-h-[7rem]" value={identity} onChange={(event) => setIdentity(event.target.value)} />
      </label>

      <label className="mt-4 grid gap-2 text-sm">
        <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.soul')}</span>
        <textarea className="construct-input min-h-[7rem]" value={soul} onChange={(event) => setSoul(event.target.value)} />
      </label>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.role')}</span>
          <select className="construct-input" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="coder">{t('agents.form.role.coder')}</option>
            <option value="reviewer">{t('agents.form.role.reviewer')}</option>
            <option value="researcher">{t('agents.form.role.researcher')}</option>
          </select>
        </label>
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.agent_type')}</span>
          <select className="construct-input" value={agentType} onChange={(event) => setAgentType(event.target.value)}>
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
        </label>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.model')}</span>
          <input className="construct-input" value={model} onChange={(event) => setModel(event.target.value)} placeholder={t('agents.form.model_placeholder')} />
        </label>
        <label className="grid gap-2 text-sm">
          <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.expertise')}</span>
          <input className="construct-input" value={expertiseInput} onChange={(event) => setExpertiseInput(event.target.value)} placeholder={t('agents.form.expertise_placeholder')} />
        </label>
      </div>

      <label className="mt-4 grid gap-2 text-sm">
        <span style={{ color: 'var(--construct-text-secondary)' }}>{t('agents.form.system_hint')}</span>
        <textarea className="construct-input min-h-[8rem]" value={systemHint} onChange={(event) => setSystemHint(event.target.value)} style={{ fontFamily: 'var(--pc-font-mono)' }} />
      </label>

      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button className="construct-button" onClick={onClose}>{t('agents.form.cancel')}</button>
        <button
          className="construct-button"
          data-variant="primary"
          disabled={saving || !canSave}
          onClick={() => onSave({
            name: name.trim(),
            identity: identity.trim(),
            soul: soul.trim(),
            expertise: expertiseInput.split(',').map((item) => item.trim()).filter(Boolean),
            tone: tone.trim(),
            role,
            agent_type: agentType,
            model: model.trim(),
            system_hint: systemHint,
            avatarFile,
          })}
        >
          {saving ? t('agents.form.saving') : mode === 'create' ? t('agents.form.save_create') : t('agents.form.save_edit')}
        </button>
      </div>
    </Modal>
  );
}
