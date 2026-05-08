import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Boxes, ChevronsLeft, ChevronsRight } from 'lucide-react';
import { v2NavSections } from './construct-navigation';
import { appAssetPath } from '@/lib/basePath';
import { useT } from '@/construct/hooks/useT';

const APP_ICON_SRC = appAssetPath('favicon-192.png');

const COLLAPSE_STORAGE_KEY = 'construct-sidebar-collapsed';

function readInitialCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

interface SidebarProps {
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
}

export default function Sidebar({ mobileOpen = false, onCloseMobile }: SidebarProps) {
  const [collapsed, setCollapsed] = useState<boolean>(readInitialCollapsed);
  const { t } = useT();

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0');
    } catch {
      // ignore
    }
  }, [collapsed]);

  const sidebarWidth = collapsed ? '4.5rem' : 'var(--construct-shell-width)';

  return (
    <>
      {mobileOpen ? (
        <div
          className="fixed inset-0 z-40 lg:hidden"
          style={{ background: 'rgba(0,0,0,0.55)' }}
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      ) : null}
      <aside
        className={`construct-sidebar fixed inset-y-0 left-0 z-50 flex flex-col overflow-hidden border-r transition-transform duration-200 lg:static lg:z-auto lg:flex lg:h-screen lg:flex-shrink-0 lg:translate-x-0 ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
        style={{
          borderColor: 'var(--construct-border-soft)',
          background: 'var(--construct-bg-shell)',
          width: sidebarWidth,
          padding: collapsed ? '1rem 0.5rem' : '1rem 0.75rem',
          transition: 'width 0.18s ease, padding 0.18s ease, transform 0.2s ease',
        }}
        data-collapsed={String(collapsed)}
      >
      {collapsed ? (
        <div className="flex flex-col items-center gap-3">
          <img
            src={APP_ICON_SRC}
            alt="Construct"
            title="Construct"
            className="h-11 w-11 rounded-[14px] object-contain"
            draggable={false}
          />
          <button
            type="button"
            className="construct-sidebar-collapse-btn"
            onClick={() => setCollapsed(false)}
            aria-label="Expand sidebar"
            title="Expand sidebar"
          >
            <ChevronsRight className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <div className="construct-panel p-4">
          <div className="flex items-center gap-3">
            <img
              src={APP_ICON_SRC}
              alt="Construct"
              className="h-11 w-11 rounded-[14px] object-contain"
              draggable={false}
            />
            <div className="min-w-0 flex-1">
              <div className="construct-kicker">Construct</div>
              <div className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                {t('sidebar.brand_subtitle')}
              </div>
            </div>
            <button
              type="button"
              className="construct-sidebar-collapse-btn"
              onClick={() => setCollapsed(true)}
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
            >
              <ChevronsLeft className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <nav
        className="construct-sidebar-scroll mt-5 min-h-0 flex-1 overflow-y-auto"
        style={{ paddingRight: collapsed ? 0 : '0.25rem' }}
      >
        <div className={collapsed ? 'space-y-4' : 'space-y-5'}>
          {v2NavSections.map((section) => (
            <section key={section.id}>
              {!collapsed ? (
                <div className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-[0.18em]" style={{ color: 'var(--construct-text-faint)' }}>
                  {t(section.labelKey)}
                </div>
              ) : (
                <div
                  className="mx-auto mb-2 h-px w-6"
                  style={{ background: 'var(--construct-border-soft)' }}
                  aria-hidden="true"
                />
              )}
              <div className="space-y-1.5">
                {section.items.map((item) => {
                  const Icon = item.icon;
                  const label = t(item.labelKey);
                  const blurb = t(item.blurbKey);
                  return (
                    <NavLink key={item.to} to={item.to} title={collapsed ? label : blurb}>
                      {({ isActive }) => (
                        <div
                          className="construct-sidebar-link w-full"
                          data-active={String(isActive)}
                          data-collapsed={String(collapsed)}
                        >
                          <Icon className="h-4 w-4 shrink-0" />
                          {!collapsed ? (
                            <div className="min-w-0">
                              <div className="text-sm font-medium">{label}</div>
                              <div className="truncate text-xs" style={{ color: isActive ? 'var(--construct-text-secondary)' : 'var(--construct-text-faint)' }}>
                                {blurb}
                              </div>
                            </div>
                          ) : null}
                        </div>
                      )}
                    </NavLink>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </nav>

      {!collapsed ? (
        <div className="construct-panel mt-5 p-4" data-variant="utility">
          <div className="flex items-center gap-2">
            <Boxes className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--construct-text-primary)' }}>{t('sidebar.shell')}</span>
          </div>
          <p className="mt-2 text-xs leading-5" style={{ color: 'var(--construct-text-secondary)' }}>
            {t('sidebar.shell_description')}
          </p>
        </div>
      ) : null}
      </aside>
    </>
  );
}
