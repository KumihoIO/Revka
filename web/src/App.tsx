import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useState, useEffect, createContext, useContext, Component, type ReactNode, type ErrorInfo } from 'react';
import { ThemeProvider } from './construct/contexts/ThemeContext';
import Landing from './pages/Landing';
import Layout from './construct/components/layout/Layout';
import Dashboard from './construct/pages/Dashboard';
import Workflows from './construct/pages/Workflows';
import WorkflowRuns from './construct/pages/WorkflowRuns';
import Teams from './construct/pages/Teams';
import Canvas from './construct/pages/Canvas';
import Agents from './construct/pages/Agents';
import Assets from './construct/pages/Assets';
import Memory from './construct/pages/Memory';
import Audit from './construct/pages/Audit';
import Logs from './construct/pages/Logs';
import Cost from './construct/pages/Cost';
import Doctor from './construct/pages/Doctor';
import Integrations from './construct/pages/Integrations';
import Skills from './construct/pages/Skills';
import Skins from './construct/pages/Skins';
import Config from './construct/pages/Config';
import Cron from './construct/pages/Cron';
import Tools from './construct/pages/Tools';
import Pairing from './construct/pages/Pairing';

import { AuthProvider, useAuth } from './hooks/useAuth';
import { DraftContext, useDraftStore } from './construct/hooks/useDraft';
import { AgentEventsProvider } from './contexts/AgentEventsContext';
import { PendingApprovalsProvider } from './contexts/PendingApprovalsContext';
import ApprovalToaster from './construct/components/approvals/ApprovalToaster';
import { setLocale, type Locale } from './lib/i18n';
import { loadLocale, saveLocale } from './contexts/localeStorage';
import { appAssetPath } from './lib/basePath';
import { getAdminPairCode } from './lib/api';

// Locale context
interface LocaleContextType {
  locale: string;
  setAppLocale: (locale: string) => void;
}

export const LocaleContext = createContext<LocaleContextType>({
  locale: 'en',
  setAppLocale: () => {},
});

export const useLocaleContext = () => useContext(LocaleContext);

// ---------------------------------------------------------------------------
// Error boundary — catches render crashes and shows a recoverable message
// instead of a black screen
// ---------------------------------------------------------------------------

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<
  { children: ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[Construct] Render error:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-6">
          <div className="card p-6 w-full max-w-lg" style={{ borderColor: 'rgba(239, 68, 68, 0.3)' }}>
            <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--color-status-error)' }}>
              Something went wrong
            </h2>
            <p className="text-sm mb-4" style={{ color: 'var(--pc-text-muted)' }}>
              A render error occurred. Check the browser console for details.
            </p>
            <pre className="text-xs rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all font-mono" style={{ background: 'var(--pc-bg-base)', color: 'var(--color-status-error)' }}>
              {this.state.error.message}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              className="btn-electric mt-6 px-4 py-2 text-sm font-medium"
            >
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// Pairing dialog component
function PairingDialog({ onPair }: { onPair: (code: string) => Promise<void> }) {
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [displayCode, setDisplayCode] = useState<string | null>(null);
  const [codeLoading, setCodeLoading] = useState(true);

  // Fetch the current pairing code from the admin endpoint (localhost only)
  useEffect(() => {
    let cancelled = false;
    getAdminPairCode()
      .then((data) => {
        if (!cancelled && data.pairing_code) {
          setDisplayCode(data.pairing_code);
        }
      })
      .catch(() => {
        // Admin endpoint not reachable (non-localhost) — user must check terminal
      })
      .finally(() => {
        if (!cancelled) setCodeLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await onPair(code);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Pairing failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--pc-bg-base)' }}>
      {/* Ambient glow */}
      <div className="relative surface-panel p-8 w-full max-w-md animate-fade-in-scale">

        <div className="text-center mb-8">
          <img
            src={appAssetPath('logo.png')}
            alt="Construct"
            className="h-20 w-20 rounded-2xl object-contain mx-auto mb-4 animate-float"
            onError={(e) => { e.currentTarget.style.display = 'none'; }}
          />
          <h1 className="text-2xl font-bold mb-2 text-gradient-blue">Construct</h1>
          <p className="text-sm" style={{ color: 'var(--pc-text-muted)' }}>
            {displayCode ? 'Your pairing code' : 'Enter the pairing code from your terminal'}
          </p>
        </div>

        {/* Show the pairing code if available (localhost) */}
        {!codeLoading && displayCode && (
          <div className="mb-6 p-4 rounded-2xl text-center border" style={{ background: 'var(--pc-accent-glow)', borderColor: 'var(--pc-accent-dim)' }}>
            <div className="text-4xl font-mono font-bold tracking-[0.4em] py-2" style={{ color: 'var(--pc-text-primary)' }}>
              {displayCode}
            </div>
            <p className="text-xs mt-2" style={{ color: 'var(--pc-text-muted)' }}>Enter this code below or on another device</p>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <input
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="6-digit code"
            className="input-electric w-full px-4 py-4 text-center text-2xl tracking-[0.3em] font-medium mb-4"
            maxLength={6}
            autoFocus
          />
          {error && (
            <p aria-live="polite" className="text-sm mb-4 text-center animate-fade-in" style={{ color: 'var(--color-status-error)' }}>{error}</p>
          )}
          <button
            type="submit"
            disabled={loading || code.length < 6}
            className="btn-electric w-full py-3.5 text-sm font-semibold tracking-wide"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <span className="h-4 w-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Pairing...
              </span>
            ) : 'Pair'}
          </button>
        </form>
      </div>
    </div>
  );
}

function AppContent() {
  const { pathname } = useLocation();
  const { isAuthenticated, requiresPairing, loading, pair, logout } = useAuth();
  const [locale, setLocaleState] = useState(loadLocale());
  const draftStore = useDraftStore();
  setLocale(locale as Locale);

  const setAppLocale = (newLocale: string) => {
    setLocaleState(newLocale);
    setLocale(newLocale as Locale);
    saveLocale(newLocale);
  };

  // Listen for 401 events to force logout
  useEffect(() => {
    const handler = () => {
      logout();
    };
    window.addEventListener('construct-unauthorized', handler);
    return () => window.removeEventListener('construct-unauthorized', handler);
  }, [logout]);

  // Landing page is always publicly accessible — skip auth checks
  if (pathname === '/') {
    return (
      <DraftContext.Provider value={draftStore}>
        <LocaleContext.Provider value={{ locale, setAppLocale }}>
          <Routes>
            <Route path="/" element={<Landing />} />
          </Routes>
        </LocaleContext.Provider>
      </DraftContext.Provider>
    );
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--pc-bg-base)' }}>
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <div className="h-10 w-10 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--pc-border)', borderTopColor: 'var(--pc-accent)' }} />
          <p className="text-sm" style={{ color: 'var(--pc-text-muted)' }}>Connecting...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated && requiresPairing) {
    return <PairingDialog onPair={pair} />;
  }

  return (
    <DraftContext.Provider value={draftStore}>
      <LocaleContext.Provider value={{ locale, setAppLocale }}>
        <AgentEventsProvider>
        <PendingApprovalsProvider>
        <ApprovalToaster />
        <Routes>
          <Route path="/memory-auditor" element={<Navigate to="/memory" replace />} />
          <Route element={<Layout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/runs" element={<WorkflowRuns />} />
            <Route path="/teams" element={<Teams />} />
            <Route path="/canvas" element={<Canvas />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/config" element={<Config />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/skins" element={<Skins />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/doctor" element={<Doctor />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/cost" element={<Cost />} />
            <Route path="/cron" element={<Cron />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/pairing" element={<Pairing />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
        </PendingApprovalsProvider>
        </AgentEventsProvider>
      </LocaleContext.Provider>
    </DraftContext.Provider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ThemeProvider>
        <AppContent />
      </ThemeProvider>
    </AuthProvider>
  );
}
