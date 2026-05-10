import { useCallback, useMemo, useState } from 'react';

/* ── types ─────────────────────────────────────────── */

export type ColorScheme = 'matrix' | 'amber' | 'cyan' | 'minimal';

export interface AssistantConfig {
  colorScheme: ColorScheme;
  fontSize: number;
  cursorBlink: boolean;
  panelHeightPercent: number;
  /** Whole-panel opacity, 0.5–1.0. Below 1.0 the page underneath shows
   *  through, useful when chatting with Operator alongside a workflow
   *  editor or canvas. */
  panelOpacity: number;
}

export interface SchemeColors {
  primary: string;
  secondary: string;
  user: string;
  system: string;
  glow: string;
  glowSecondary: string;
  cursorColor: string;
}

/* ── color schemes ─────────────────────────────────── */

export const COLOR_SCHEMES: Record<ColorScheme, { label: string; colors: SchemeColors }> = {
  matrix: {
    label: 'Matrix',
    colors: {
      primary: '#7dff9b',
      secondary: '#72d8ff',
      user: '#e6e6e6',
      system: '#72d8ff',
      glow: '0 0 6px rgba(125,255,155,0.25)',
      glowSecondary: '0 0 6px rgba(114,216,255,0.25)',
      cursorColor: '#7dff9b',
    },
  },
  amber: {
    label: 'Amber',
    colors: {
      primary: '#ffc857',
      secondary: '#ff9f43',
      user: '#e6e6e6',
      system: '#ff9f43',
      glow: '0 0 6px rgba(255,200,87,0.25)',
      glowSecondary: '0 0 6px rgba(255,159,67,0.25)',
      cursorColor: '#ffc857',
    },
  },
  cyan: {
    label: 'Cyan',
    colors: {
      primary: '#72d8ff',
      secondary: '#a78bfa',
      user: '#e6e6e6',
      system: '#a78bfa',
      glow: '0 0 6px rgba(114,216,255,0.25)',
      glowSecondary: '0 0 6px rgba(167,139,250,0.25)',
      cursorColor: '#72d8ff',
    },
  },
  minimal: {
    label: 'Minimal',
    colors: {
      primary: '#b0b0b0',
      secondary: '#808080',
      user: '#e6e6e6',
      system: '#808080',
      glow: 'none',
      glowSecondary: 'none',
      cursorColor: '#e6e6e6',
    },
  },
};

export const SCHEME_KEYS = Object.keys(COLOR_SCHEMES) as ColorScheme[];

/* ── defaults + persistence ────────────────────────── */

const STORAGE_KEY = 'construct-assistant-config';

const DEFAULT_CONFIG: AssistantConfig = {
  colorScheme: 'matrix',
  fontSize: 13,
  cursorBlink: true,
  panelHeightPercent: 60,
  panelOpacity: 1.0,
};

function loadConfig(): AssistantConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_CONFIG;
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_CONFIG;
  }
}

function saveConfig(config: AssistantConfig) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch { /* localStorage may be full or disabled */ }
}

/* ── hook ──────────────────────────────────────────── */

export function useAssistantConfig() {
  const [config, setConfig] = useState<AssistantConfig>(loadConfig);

  const colors = useMemo(() => COLOR_SCHEMES[config.colorScheme].colors, [config.colorScheme]);

  const updateConfig = useCallback((partial: Partial<AssistantConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...partial };
      saveConfig(next);
      return next;
    });
  }, []);

  const resetConfig = useCallback(() => {
    setConfig(DEFAULT_CONFIG);
    saveConfig(DEFAULT_CONFIG);
  }, []);

  return { config, colors, updateConfig, resetConfig };
}
