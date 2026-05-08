import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Loader2, Menu, MessageSquare, MonitorCog, MoonStar, ShieldAlert, ShieldCheck, SunMedium } from 'lucide-react';
import { useTheme } from '@/construct/hooks/useTheme';
import { useT } from '@/construct/hooks/useT';
import { verifyAuditChain } from '@/lib/api';
import type { AuditVerifyResponse } from '@/types/api';
import { useV2Assistant } from '../assistant/AssistantContext';
import { v2RouteMeta } from './construct-navigation';
import ApprovalBadge from '../approvals/ApprovalBadge';
import LanguageSwitcher from './LanguageSwitcher';

interface HeaderProps {
  onOpenMobileNav?: () => void;
}

export default function Header({ onOpenMobileNav }: HeaderProps) {
  const location = useLocation();
  const { theme, resolvedTheme, setTheme } = useTheme();
  const { open, toggleAssistant } = useV2Assistant();
  const { t } = useT();
  const meta = v2RouteMeta[location.pathname];
  const title = meta ? t(meta.titleKey) : 'Construct';

  const [audit, setAudit] = useState<AuditVerifyResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      verifyAuditChain()
        .then((res) => {
          if (!cancelled) setAudit(res);
        })
        .catch((err) => {
          if (!cancelled) {
            setAudit({ verified: false, error: err instanceof Error ? err.message : String(err) });
          }
        });
    };
    check();
    const id = window.setInterval(check, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return (
    <header className="px-4 py-3 lg:px-6 lg:py-4">
      <div className="construct-panel p-3 lg:p-4">
        {/* Mobile: compact single-row header with title + right-aligned hamburger + operator.
            Desktop (lg+): full layout with pills and theme toggles. */}
        <div className="flex items-center gap-2 lg:hidden">
          <div className="min-w-0 flex-1">
            <div className="construct-kicker text-[10px]">Construct</div>
            <h1 className="construct-title mt-0.5 truncate text-lg">{title}</h1>
          </div>
          <ApprovalBadge />
          <button
            type="button"
            className="construct-button px-2 py-1 text-xs"
            onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'system' : 'dark')}
            aria-label={`Theme: ${theme}. Tap to cycle.`}
            title={`Theme: ${theme === 'system' ? `system (${resolvedTheme})` : theme}`}
          >
            {theme === 'dark' ? (
              <MoonStar className="h-4 w-4" />
            ) : theme === 'light' ? (
              <SunMedium className="h-4 w-4" />
            ) : (
              <MonitorCog className="h-4 w-4" />
            )}
          </button>
          <button
            type="button"
            className="construct-button px-2 py-1 text-xs"
            onClick={toggleAssistant}
            aria-label="Toggle operator"
            style={open ? { background: 'var(--construct-signal-live-soft)', color: 'var(--construct-signal-live)', borderColor: 'var(--construct-signal-live)' } : undefined}
          >
            <MessageSquare className="h-4 w-4" />
          </button>
          {onOpenMobileNav ? (
            <button
              type="button"
              className="construct-sidebar-collapse-btn"
              onClick={onOpenMobileNav}
              aria-label="Open navigation"
              title="Open navigation"
            >
              <Menu className="h-4 w-4" />
            </button>
          ) : null}
        </div>

        <div className="hidden flex-col gap-4 lg:flex lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="construct-kicker text-[10px]">Construct</div>
            <h1 className="construct-title mt-1 truncate text-2xl">{title}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ApprovalBadge />
            {/* Single combined health pill — runtime + trust state read
                from a single glance instead of two adjacent pills both
                saying "ok". Keeps the trust-verified accent on the icon
                so the brand-defining shield is still front and centre. */}
            <span
              className="construct-status-pill"
              title={
                audit === null
                  ? t('header.trust_checking')
                  : audit.verified
                    ? `${t('header.runtime_healthy')} · ${t('header.trust_verified')}`
                    : (audit.error ?? t('header.trust_unverified'))
              }
              style={
                audit?.verified
                  ? {
                      color: 'var(--construct-signal-live)',
                      borderColor: 'color-mix(in srgb, var(--construct-signal-live) 40%, transparent)',
                      background: 'color-mix(in srgb, var(--construct-signal-live) 10%, transparent)',
                    }
                  : audit && !audit.verified
                    ? {
                        color: 'var(--construct-status-warning)',
                        borderColor: 'color-mix(in srgb, var(--construct-status-warning) 40%, transparent)',
                        background: 'color-mix(in srgb, var(--construct-status-warning) 10%, transparent)',
                      }
                    : undefined
              }
            >
              <span className="construct-dot" style={{ background: audit?.verified === false ? 'var(--construct-status-warning)' : 'var(--construct-signal-live)' }} />
              {audit === null ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : audit.verified ? (
                <ShieldCheck className="h-3.5 w-3.5" />
              ) : (
                <ShieldAlert className="h-3.5 w-3.5" />
              )}
              {audit?.verified ? t('header.trust_verified') : audit && !audit.verified ? t('header.trust_unverified') : t('header.trust_checking')}
            </span>
            <LanguageSwitcher />
            {/* Theme toggle — icon-only buttons (text labels removed; the
                pill duplicating the active mode was also dropped, since the
                active state on this group already conveys the same info). */}
            <div className="construct-theme-toggle" role="group" aria-label={t('theme.mode')}>
              <button
                type="button"
                className="construct-theme-toggle-button"
                data-active={String(theme === 'dark')}
                onClick={() => setTheme('dark')}
                title={t('theme.dark')}
                aria-label={t('theme.dark')}
              >
                <MoonStar className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="construct-theme-toggle-button"
                data-active={String(theme === 'light')}
                onClick={() => setTheme('light')}
                title={t('theme.light')}
                aria-label={t('theme.light')}
              >
                <SunMedium className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="construct-theme-toggle-button"
                data-active={String(theme === 'system')}
                onClick={() => setTheme('system')}
                title={t('theme.system')}
                aria-label={t('theme.system')}
              >
                <MonitorCog className="h-4 w-4" />
              </button>
            </div>
            <button
              type="button"
              className="construct-button text-sm"
              onClick={toggleAssistant}
              style={open ? { background: 'var(--construct-signal-live-soft)', color: 'var(--construct-signal-live)', borderColor: 'var(--construct-signal-live)' } : undefined}
            >
              <MessageSquare className="h-4 w-4" />
              {t('header.operator')}
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
