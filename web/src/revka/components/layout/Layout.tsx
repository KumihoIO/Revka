import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { useTheme } from '@/revka/hooks/useTheme';
import AssistantPanel from '../assistant/AssistantPanel';
import { V2AssistantProvider } from '../assistant/AssistantContext';
import Header from './Header';
import Sidebar from './Sidebar';

export default function Layout() {
  const { theme, resolvedTheme } = useTheme();
  const v2Theme = theme === 'system' ? resolvedTheme : theme;
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = v2Theme;
    return () => {
      if (root.dataset.theme === v2Theme) {
        delete root.dataset.theme;
      }
    };
  }, [v2Theme]);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  return (
    <V2AssistantProvider>
      <div className="revka-shell lg:flex lg:h-screen lg:overflow-hidden">
        <Sidebar mobileOpen={mobileNavOpen} onCloseMobile={() => setMobileNavOpen(false)} />
        <div className="min-w-0 flex-1 lg:flex lg:h-screen lg:flex-col lg:overflow-hidden">
          <Header onOpenMobileNav={() => setMobileNavOpen(true)} />
          <div className="relative min-h-0 flex-1">
            <main className="h-full overflow-y-auto px-4 pb-6 lg:px-6 lg:pb-8">
              <Outlet />
            </main>
            <AssistantPanel />
          </div>
        </div>
      </div>
    </V2AssistantProvider>
  );
}
