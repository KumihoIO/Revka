import { useEffect, useMemo, useState } from 'react';
import { ExternalLink, Pencil, Plus, Power, Search, Sparkles, Trash2 } from 'lucide-react';
import type { ClawHubSearchResult, SkillCreateRequest, SkillDefinition, SkillUpdateRequest } from '@/types/api';
import { createSkill, deleteSkill, fetchClawHubTrending, fetchSkills, installClawHubSkill, searchClawHub, toggleSkillDeprecation, updateSkill } from '@/lib/api';
import { useT } from '@/construct/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import Modal from '../components/ui/Modal';
import Notice from '../components/ui/Notice';
import StateMessage from '../components/ui/StateMessage';

const DOMAINS = ['Memory', 'Creative', 'Privacy', 'Graph', 'Behavioral', 'Other'] as const;

export default function Skills() {
  const { t, tpl } = useT();
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSkillKref, setSelectedSkillKref] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [editorMode, setEditorMode] = useState<'create' | 'edit' | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [marketplaceQuery, setMarketplaceQuery] = useState('');
  const [marketplaceResults, setMarketplaceResults] = useState<ClawHubSearchResult[]>([]);
  const [marketplaceLoading, setMarketplaceLoading] = useState(true);
  const [marketplaceError, setMarketplaceError] = useState<string | null>(null);
  const [githubUrl, setGithubUrl] = useState('');
  const [githubInstalling, setGithubInstalling] = useState(false);

  const load = async () => {
    setLoading(true);
    return fetchSkills(true, 1, 100)
      .then((page) => {
        setSkills(page.skills);
        setSelectedSkillKref((current) => current ?? page.skills[0]?.kref ?? null);
      })
      .catch(() => setSkills([]))
      .finally(() => setLoading(false));
  };

  const loadMarketplace = async (query?: string) => {
    setMarketplaceLoading(true);
    setMarketplaceError(null);
    try {
      const trimmed = query?.trim() ?? '';
      let results: ClawHubSearchResult[];
      if (trimmed.length > 1) {
        results = await searchClawHub(trimmed, 12);
      } else {
        results = await fetchClawHubTrending(12);
        if (results.length === 0) {
          results = await searchClawHub('agent', 12);
        }
      }
      setMarketplaceResults(results);
    } catch (err) {
      console.error('ClawHub marketplace load failed:', err);
      setMarketplaceResults([]);
      setMarketplaceError(err instanceof Error ? err.message : String(err));
    } finally {
      setMarketplaceLoading(false);
    }
  };

  useEffect(() => {
    load();
    loadMarketplace();
  }, []);

  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.kref === selectedSkillKref) ?? skills[0] ?? null,
    [selectedSkillKref, skills],
  );

  const filteredSkills = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return skills;
    return skills.filter((skill) => (
      skill.name.toLowerCase().includes(query)
      || skill.description.toLowerCase().includes(query)
      || skill.domain.toLowerCase().includes(query)
      || skill.tags.some((tag) => tag.toLowerCase().includes(query))
    ));
  }, [search, skills]);

  const skillSummary = useMemo(() => ({
    total: skills.length,
    active: skills.filter((skill) => !skill.deprecated).length,
    deprecated: skills.filter((skill) => skill.deprecated).length,
    domains: new Set(skills.map((skill) => skill.domain)).size,
  }), [skills]);

  const handleSave = async (values: SkillFormValues) => {
    setSaving(true);
    try {
      if (editorMode === 'edit' && selectedSkill) {
        const request: SkillUpdateRequest = { ...values, kref: selectedSkill.kref };
        const updated = await updateSkill(request);
        await load();
        setSelectedSkillKref(updated.kref);
        setNotice({ tone: 'success', message: tpl('skills.toast.updated', { name: updated.name }) });
      } else {
        const request: SkillCreateRequest = values;
        const created = await createSkill(request);
        await load();
        setSelectedSkillKref(created.kref);
        setNotice({ tone: 'success', message: tpl('skills.toast.created', { name: created.name }) });
      }
      setEditorMode(null);
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('skills.err.save') });
    } finally {
      setSaving(false);
    }
  };

  const handleToggleDeprecation = async () => {
    if (!selectedSkill) return;
    try {
      await toggleSkillDeprecation(selectedSkill.kref, !selectedSkill.deprecated);
      await load();
      setNotice({ tone: 'success', message: tpl(selectedSkill.deprecated ? 'skills.toast.reenabled' : 'skills.toast.deprecated', { name: selectedSkill.name }) });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('skills.err.toggle') });
    }
  };

  const handleDelete = async () => {
    if (!selectedSkill) return;
    setDeleting(true);
    try {
      const name = selectedSkill.name;
      await deleteSkill(selectedSkill.kref);
      setSelectedSkillKref(null);
      await load();
      setNotice({ tone: 'success', message: tpl('skills.toast.deleted', { name }) });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('skills.err.delete') });
    } finally {
      setDeleting(false);
    }
  };

  const handleInstall = async (slug: string) => {
    try {
      const installed = await installClawHubSkill(slug);
      await load();
      setNotice({ tone: 'success', message: tpl('skills.toast.installed', { name: installed.name }) });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('skills.err.install') });
    }
  };

  const handleGithubInstall = async () => {
    const url = githubUrl.trim();
    if (!url) return;
    setGithubInstalling(true);
    try {
      const installed = await installClawHubSkill(url);
      await load();
      setGithubUrl('');
      setNotice({ tone: 'success', message: tpl('skills.toast.installed_github', { name: installed.name }) });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('skills.err.install_github') });
    } finally {
      setGithubInstalling(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}
      <PageHeader
        kicker={t('skills.kicker')}
        title={t('skills.title')}
        actions={(
          <button className="construct-button" data-variant="primary" onClick={() => setEditorMode('create')}>
            <Plus className="h-4 w-4" />
            {t('skills.create_skill')}
          </button>
        )}
      />

      <div className="construct-skills-grid grid min-h-0 flex-1 gap-4">
        <div className="flex min-h-0 flex-col gap-4">
          <Panel className="p-4" variant="secondary">
            <div className="construct-kicker">{t('skills.registry_status')}</div>
            <div className="mt-3 grid grid-cols-2 gap-2 xl:grid-cols-4">
              <SummaryMetric label={t('skills.summary.total')} value={skillSummary.total} />
              <SummaryMetric label={t('skills.summary.active')} value={skillSummary.active} />
              <SummaryMetric label={t('skills.summary.deprecated')} value={skillSummary.deprecated} />
              <SummaryMetric label={t('skills.summary.domains')} value={skillSummary.domains} />
            </div>
          </Panel>

          <Panel className="flex min-h-0 flex-1 flex-col p-4" variant="secondary">
            <div className="flex items-center justify-between gap-2">
              <div className="construct-kicker">{t('skills.index')}</div>
              <span className="text-[11px]" style={{ color: 'var(--construct-text-faint)' }}>
                {filteredSkills.length} / {skills.length}
              </span>
            </div>
            <div className="relative mt-3">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--construct-text-faint)' }} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="construct-input pl-10"
                placeholder={t('skills.search_placeholder')}
              />
            </div>
            <div className="mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
              {loading ? (
                <StateMessage compact tone="loading" title={t('skills.loading')} />
              ) : filteredSkills.length === 0 ? (
                <StateMessage compact title={t('skills.no_skills_title')} description={t('skills.no_skills_desc')} />
              ) : (
                filteredSkills.map((skill) => (
                  <button
                    key={skill.kref}
                    type="button"
                    onClick={() => setSelectedSkillKref(skill.kref)}
                    className="w-full rounded-[14px] border px-3 py-3.5 text-left transition"
                    style={{
                      opacity: skill.deprecated ? 0.65 : 1,
                      borderColor: skill.kref === selectedSkill?.kref ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)',
                      background: skill.kref === selectedSkill?.kref
                        ? 'color-mix(in srgb, var(--construct-signal-live-soft) 80%, var(--construct-bg-panel-strong))'
                        : 'color-mix(in srgb, var(--construct-bg-panel-strong) 94%, transparent)',
                    }}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]" style={{ background: 'rgba(114,216,255,0.12)', color: 'var(--construct-signal-network)' }}>
                        <Sparkles className="h-4 w-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <div className="text-sm font-semibold leading-5" style={{ color: 'var(--construct-text-primary)' }}>{skill.name}</div>
                          <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: skill.deprecated ? 'rgba(255,107,122,0.12)' : 'rgba(125,255,155,0.12)', color: skill.deprecated ? 'var(--construct-status-warning)' : 'var(--construct-status-success)' }}>
                            {skill.deprecated ? t('skills.status.deprecated') : t('skills.status.active')}
                          </span>
                        </div>
                        <div className="mt-1 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{skill.domain}</div>
                        <div className="mt-1.5 line-clamp-2 text-[11px] leading-4" style={{ color: 'var(--construct-text-faint)' }}>{skill.description}</div>
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </Panel>
        </div>

        <Panel className="overflow-y-auto p-5">
          <div className="construct-kicker">{t('skills.workspace')}</div>
          <h3 className="mt-2 text-lg font-semibold">{selectedSkill?.name ?? t('skills.select_placeholder')}</h3>
          {selectedSkill ? (
            <div className="mt-4 space-y-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
                    <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{selectedSkill.name}</div>
                  </div>
                  <div className="mt-1 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
                    {selectedSkill.domain} · rev {selectedSkill.revision_number} · {new Date(selectedSkill.created_at).toLocaleDateString()}
                  </div>
                </div>
                <span className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{
                  background: selectedSkill.deprecated ? 'rgba(255,107,122,0.12)' : 'var(--construct-signal-live-soft)',
                  color: selectedSkill.deprecated ? 'var(--construct-status-warning)' : 'var(--construct-status-success)',
                }}>
                  {selectedSkill.deprecated ? t('skills.status.deprecated') : t('skills.status.active')}
                </span>
              </div>

              <div className="flex flex-wrap gap-2">
                <span className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: 'var(--construct-signal-live-soft)', color: 'var(--construct-signal-live)' }}>
                  {selectedSkill.domain}
                </span>
                {selectedSkill.tags.map((tag) => (
                  <span key={tag} className="rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-secondary)' }}>
                    {tag}
                  </span>
                ))}
              </div>

              <Panel className="p-4" variant="utility">
                <div className="construct-kicker">{t('skills.description')}</div>
                <div className="mt-3 text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
                  {selectedSkill.description}
                </div>
              </Panel>

              <div>
                <div className="construct-kicker">{t('skills.content')}</div>
                <pre className="mt-2 max-h-[24rem] overflow-auto rounded-[14px] border p-4 text-xs leading-6" style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-secondary)', fontFamily: 'var(--pc-font-mono)', background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 94%, transparent)' }}>
                  {selectedSkill.content}
                </pre>
              </div>

              <div className="flex gap-2">
                <button className="construct-button" onClick={() => setEditorMode('edit')}>
                  <Pencil className="h-4 w-4" />
                  {t('skills.edit')}
                </button>
                <button className="construct-button" onClick={handleToggleDeprecation}>
                  <Power className="h-4 w-4" />
                  {selectedSkill.deprecated ? t('skills.reenable') : t('skills.deprecate')}
                </button>
                <button className="construct-button" onClick={handleDelete} disabled={deleting}>
                  <Trash2 className="h-4 w-4" />
                  {deleting ? t('skills.deleting') : t('skills.delete')}
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-4">
              <StateMessage title={t('skills.no_selected_title')} description={t('skills.no_selected_desc')} />
            </div>
          )}
        </Panel>

        <div className="flex min-h-0 flex-col gap-4">
          <Panel className="p-4" variant="utility">
            <div className="construct-kicker">{t('skills.install_github')}</div>
            <p className="mt-2 text-xs leading-5" style={{ color: 'var(--construct-text-secondary)' }}>
              {t('skills.install_github_desc')}
            </p>
            <div className="mt-3 flex gap-2">
              <div className="relative min-w-0 flex-1">
                <ExternalLink className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--construct-text-faint)' }} />
                <input
                  className="construct-input pl-10"
                  value={githubUrl}
                  onChange={(event) => setGithubUrl(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') handleGithubInstall();
                  }}
                  placeholder={t('skills.github_placeholder')}
                />
              </div>
              <button
                className="construct-button shrink-0"
                data-variant="primary"
                disabled={!githubUrl.trim() || githubInstalling}
                onClick={handleGithubInstall}
              >
                {githubInstalling ? t('skills.installing') : t('skills.install')}
              </button>
            </div>
          </Panel>

          <Panel className="flex min-h-0 flex-1 flex-col p-4" variant="utility">
            <div className="flex items-center justify-between gap-3">
              <div className="construct-kicker">{t('skills.marketplace')}</div>
              <div className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{tpl('skills.marketplace_shown', { count: marketplaceResults.length })}</div>
            </div>
            <div className="relative mt-3">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--construct-text-faint)' }} />
              <input
                className="construct-input pl-10"
                value={marketplaceQuery}
                onChange={(event) => setMarketplaceQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') loadMarketplace(marketplaceQuery);
                }}
                placeholder={t('skills.marketplace_placeholder')}
              />
            </div>
            <div className="mt-3 flex gap-2">
              <button className="construct-button" onClick={() => loadMarketplace(marketplaceQuery)}>{t('skills.search')}</button>
              <button className="construct-button" onClick={() => { setMarketplaceQuery(''); loadMarketplace(); }}>{t('skills.trending')}</button>
            </div>
            <div className="mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
              {marketplaceLoading ? (
                <StateMessage compact tone="loading" title={t('skills.marketplace_loading')} />
              ) : marketplaceError ? (
                <StateMessage compact tone="error" title={t('skills.marketplace_empty')} description={marketplaceError} />
              ) : marketplaceResults.length === 0 ? (
                <StateMessage compact title={t('skills.marketplace_empty')} />
              ) : (
                marketplaceResults.map((skill) => (
                  <div key={skill.slug} className="rounded-[12px] border p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{skill.displayName || skill.name || skill.slug}</div>
                        <div className="mt-1 line-clamp-2 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{skill.description}</div>
                      </div>
                      <button className="construct-button shrink-0" onClick={() => handleInstall(skill.slug)}>{t('skills.install')}</button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </div>
      </div>

      {editorMode ? (
        <SkillEditorModal
          skill={editorMode === 'edit' ? selectedSkill : null}
          saving={saving}
          onClose={() => setEditorMode(null)}
          onSave={handleSave}
        />
      ) : null}
    </div>
  );
}

function SummaryMetric({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-surface)' }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>{label}</div>
      <div className="mt-2 text-xl font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{value}</div>
    </div>
  );
}

interface SkillFormValues {
  name: string;
  description: string;
  content: string;
  domain: string;
  tags: string[];
}

function SkillEditorModal({
  skill,
  saving,
  onClose,
  onSave,
}: {
  skill: SkillDefinition | null;
  saving: boolean;
  onClose: () => void;
  onSave: (values: SkillFormValues) => void;
}) {
  const { t } = useT();
  const [name, setName] = useState(skill?.name ?? '');
  const [description, setDescription] = useState(skill?.description ?? '');
  const [content, setContent] = useState(skill?.content ?? '');
  const [domain, setDomain] = useState(skill?.domain ?? 'Other');
  const [tags, setTags] = useState(skill?.tags.join(', ') ?? '');

  useEffect(() => {
    setName(skill?.name ?? '');
    setDescription(skill?.description ?? '');
    setContent(skill?.content ?? '');
    setDomain(skill?.domain ?? 'Other');
    setTags(skill?.tags.join(', ') ?? '');
  }, [skill?.content, skill?.description, skill?.domain, skill?.name, skill?.tags]);

  return (
    <Modal title={skill ? t('skills.modal.edit_title') : t('skills.modal.create_title')} description={t('skills.modal.description')} onClose={onClose}>
      <div className="grid gap-4">
        <input className="construct-input" value={name} onChange={(event) => setName(event.target.value)} placeholder={t('skills.modal.name_placeholder')} />
        <textarea className="construct-input min-h-24" value={description} onChange={(event) => setDescription(event.target.value)} placeholder={t('skills.modal.description_placeholder')} />
        <select className="construct-input" value={domain} onChange={(event) => setDomain(event.target.value)}>
          {DOMAINS.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
        <input className="construct-input" value={tags} onChange={(event) => setTags(event.target.value)} placeholder={t('skills.modal.tags_placeholder')} />
        <textarea className="construct-input min-h-72" value={content} onChange={(event) => setContent(event.target.value)} placeholder={t('skills.modal.content_placeholder')} style={{ fontFamily: 'var(--pc-font-mono)' }} />
        <div className="flex justify-end gap-3">
          <button className="construct-button" onClick={onClose}>{t('skills.modal.cancel')}</button>
          <button
            className="construct-button"
            data-variant="primary"
            disabled={!name.trim() || !description.trim() || !content.trim() || saving}
            onClick={() => onSave({ name: name.trim(), description: description.trim(), content, domain, tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean) })}
          >
            {saving ? t('skills.modal.saving') : skill ? t('skills.modal.save_skill') : t('skills.create_skill')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
