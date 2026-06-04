import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { ThemeContext, type ThemeContextValue } from './ThemeContextDef';
import { loadStored, STORAGE_KEY } from './themeStorage';
import type { ThemeMode, AccentColor, UiFont, MonoFont } from './ThemeContextDef';
import { uiFontStacks, monoFontStacks } from './ThemeContextDef';
import { loadUiFont, loadMonoFont } from './fontLoader';
import { colorThemeMap, DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, type ColorThemeId } from './colorThemes';
import { SKIN_ASSET_SLOTS, type SkinAssetSlot, type SkinModeDefinition, type SkinSummary } from '@/types/api';
import {
  deleteSkin as apiDeleteSkin,
  getSkins as apiGetSkins,
  importSkinZip as apiImportSkinZip,
} from '@/lib/api';
import { skinAssetPath } from '@/lib/basePath';
import { useAuth } from '@/hooks/useAuth';

/** Accent-only overrides (applied on top of color theme when user picks a custom accent). */
const accents: Record<AccentColor, Record<string, string>> = {
  cyan: {
    '--pc-accent': '#22d3ee',
    '--pc-accent-light': '#67e8f9',
    '--pc-accent-dim': 'rgba(34,211,238,0.3)',
    '--pc-accent-glow': 'rgba(34,211,238,0.1)',
    '--pc-accent-rgb': '34,211,238',
  },
  violet: {
    '--pc-accent': '#8b5cf6',
    '--pc-accent-light': '#a78bfa',
    '--pc-accent-dim': 'rgba(139,92,246,0.3)',
    '--pc-accent-glow': 'rgba(139,92,246,0.1)',
    '--pc-accent-rgb': '139,92,246',
  },
  emerald: {
    '--pc-accent': '#10b981',
    '--pc-accent-light': '#34d399',
    '--pc-accent-dim': 'rgba(16,185,129,0.3)',
    '--pc-accent-glow': 'rgba(16,185,129,0.1)',
    '--pc-accent-rgb': '16,185,129',
  },
  amber: {
    '--pc-accent': '#f59e0b',
    '--pc-accent-light': '#fbbf24',
    '--pc-accent-dim': 'rgba(245,158,11,0.3)',
    '--pc-accent-glow': 'rgba(245,158,11,0.1)',
    '--pc-accent-rgb': '245,158,11',
  },
  rose: {
    '--pc-accent': '#f43f5e',
    '--pc-accent-light': '#fb7185',
    '--pc-accent-dim': 'rgba(244,63,94,0.3)',
    '--pc-accent-glow': 'rgba(244,63,94,0.1)',
    '--pc-accent-rgb': '244,63,94',
  },
  blue: {
    '--pc-accent': '#3b82f6',
    '--pc-accent-light': '#60a5fa',
    '--pc-accent-dim': 'rgba(59,130,246,0.3)',
    '--pc-accent-glow': 'rgba(59,130,246,0.1)',
    '--pc-accent-rgb': '59,130,246',
  },
};

function applyVars(vars: Record<string, string>) {
  const root = document.documentElement;
  for (const [k, v] of Object.entries(vars)) {
    if (k === '--color-scheme') {
      root.style.colorScheme = v as 'light' | 'dark';
    } else {
      root.style.setProperty(k, v);
    }
  }
}

let appliedSkinVarNames = new Set<string>();

function clearStaleSkinVars(nextVars: Record<string, string>) {
  const root = document.documentElement;
  for (const name of appliedSkinVarNames) {
    if (!(name in nextVars)) {
      root.style.removeProperty(name);
    }
  }
  appliedSkinVarNames = new Set(Object.keys(nextVars));
}

const pcBridge: Record<string, string[]> = {
  '--revka-bg-base': ['--pc-bg-base'],
  '--revka-bg-surface': ['--pc-bg-surface'],
  '--revka-bg-elevated': ['--pc-bg-elevated', '--pc-bg-code'],
  '--revka-bg-input': ['--pc-bg-input'],
  '--revka-bg-shell': ['--pc-bg-sidebar'],
  '--revka-border-soft': ['--pc-border'],
  '--revka-border-strong': ['--pc-border-strong'],
  '--revka-text-primary': ['--pc-text-primary'],
  '--revka-text-secondary': ['--pc-text-secondary'],
  '--revka-text-muted': ['--pc-text-muted'],
  '--revka-text-faint': ['--pc-text-faint'],
  '--revka-signal-live': ['--pc-accent'],
  '--revka-signal-selected': ['--pc-accent-light'],
  '--revka-signal-live-soft': ['--pc-accent-glow'],
  '--revka-signal-network-soft': ['--pc-hover'],
  '--revka-border-neutral': ['--pc-separator'],
};

function hexToRgbTriplet(value: string): string | null {
  const raw = value.trim().toLowerCase();
  if (!raw.startsWith('#')) return null;
  const hex = raw.slice(1);
  if (hex.length === 3 || hex.length === 4) {
    const [r, g, b] = hex.slice(0, 3).split('').map((part) => parseInt(part + part, 16));
    return [r, g, b].every((part) => Number.isFinite(part)) ? `${r}, ${g}, ${b}` : null;
  }
  if (hex.length === 6 || hex.length === 8) {
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    return [r, g, b].every((part) => Number.isFinite(part)) ? `${r}, ${g}, ${b}` : null;
  }
  return null;
}

function bridgePcVars(tokens: Record<string, string>): Record<string, string> {
  const bridged: Record<string, string> = {};
  for (const [revkaToken, pcTokens] of Object.entries(pcBridge)) {
    const value = tokens[revkaToken];
    if (!value) continue;
    for (const pcToken of pcTokens) {
      bridged[pcToken] = value;
    }
  }
  const live = tokens['--revka-signal-live'];
  if (live) {
    const rgb = hexToRgbTriplet(live);
    if (rgb) bridged['--pc-accent-rgb'] = rgb;
    bridged['--pc-accent-dim'] = `color-mix(in srgb, ${live} 30%, transparent)`;
    bridged['--pc-accent-glow'] = tokens['--revka-signal-live-soft'] ?? `color-mix(in srgb, ${live} 12%, transparent)`;
    bridged['--pc-accent-glow-strong'] = `color-mix(in srgb, ${live} 22%, transparent)`;
  }
  return bridged;
}

/** Resolve which color theme to use based on the mode. */
function resolveColorTheme(mode: ThemeMode, colorTheme: ColorThemeId): ColorThemeId {
  if (mode === 'system') {
    const preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    const ct = colorThemeMap[colorTheme];
    // If the selected theme matches system preference, use it; otherwise pick the right default
    if (ct && ((preferLight && ct.scheme === 'light') || (!preferLight && ct.scheme === 'dark'))) {
      return colorTheme;
    }
    return preferLight ? DEFAULT_LIGHT_THEME : DEFAULT_DARK_THEME;
  }
  if (mode === 'oled') return 'oled-black';
  if (mode === 'light') {
    const ct = colorThemeMap[colorTheme];
    return ct?.scheme === 'light' ? colorTheme : DEFAULT_LIGHT_THEME;
  }
  if (mode === 'dark') {
    const ct = colorThemeMap[colorTheme];
    return ct?.scheme === 'dark' ? colorTheme : DEFAULT_DARK_THEME;
  }
  return colorTheme;
}

function resolveThemeScheme(mode: ThemeMode, colorTheme: ColorThemeId): 'dark' | 'light' | 'oled' {
  if (mode === 'oled') return 'oled';
  if (mode === 'light' || mode === 'dark') return mode;
  const resolved = resolveColorTheme(mode, colorTheme);
  const ct = colorThemeMap[resolved];
  return ct?.scheme ?? 'dark';
}

interface ThemeSettings {
  theme: ThemeMode;
  accent: AccentColor;
  colorTheme: ColorThemeId;
  uiFont: UiFont;
  monoFont: MonoFont;
  uiFontSize: number;
  monoFontSize: number;
  activeSkinId: string | null;
}

function fontVars(uiFont: UiFont, monoFont: MonoFont, uiFontSize: number, monoFontSize: number) {
  return {
    '--pc-font-ui': uiFontStacks[uiFont],
    '--pc-font-mono': monoFontStacks[monoFont],
    '--pc-font-size': `${uiFontSize}px`,
    '--pc-font-size-mono': `${monoFontSize}px`,
  };
}

function modeForScheme(skin: SkinSummary | null, scheme: 'dark' | 'light' | 'oled'): SkinModeDefinition | null {
  if (!skin) return null;
  const preferred = scheme === 'light' ? skin.manifest.modes.light : skin.manifest.modes.dark;
  return preferred ?? skin.manifest.modes.dark ?? skin.manifest.modes.light ?? null;
}

function cssUrl(url: string): string {
  return `url("${url.replace(/"/g, '%22')}")`;
}

function skinAssetVars(
  skin: SkinSummary | null,
  assets: Partial<Record<SkinAssetSlot, string>>,
): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const slot of SKIN_ASSET_SLOTS) {
    const asset = assets[slot];
    vars[`--revka-skin-${slot}`] = asset && skin ? cssUrl(skinAssetPath(skin.id, asset)) : 'none';
  }
  return vars;
}

function deriveSkinTokenVars(tokens: Record<string, string>): Record<string, string> {
  const derived: Record<string, string> = {};
  const base = tokens['--revka-bg-base'];
  const surface = tokens['--revka-bg-surface'];
  const panel = tokens['--revka-bg-panel'];
  const panelStrong = tokens['--revka-bg-panel-strong'];
  const borderSoft = tokens['--revka-border-soft'];
  const live = tokens['--revka-signal-live'];
  const network = tokens['--revka-signal-network'];
  const selected = tokens['--revka-signal-selected'] ?? live;
  const inputSurface = panelStrong ?? surface ?? panel;
  const success = tokens['--revka-status-success'] ?? selected ?? live;

  if (inputSurface && !tokens['--revka-bg-input']) {
    derived['--revka-bg-input'] = `color-mix(in srgb, ${inputSurface} 88%, ${base ?? 'black'})`;
  }
  if ((borderSoft || selected) && !tokens['--revka-border-neutral']) {
    const neutral = selected
      ? `color-mix(in srgb, ${selected} 18%, ${borderSoft ?? 'transparent'})`
      : borderSoft;
    if (neutral) derived['--revka-border-neutral'] = neutral;
  }
  if (live && !tokens['--revka-signal-live-soft']) {
    derived['--revka-signal-live-soft'] = `color-mix(in srgb, ${live} 16%, transparent)`;
  }
  if (network && !tokens['--revka-signal-network-soft']) {
    derived['--revka-signal-network-soft'] = `color-mix(in srgb, ${network} 14%, transparent)`;
  }
  if (selected && !tokens['--revka-signal-selected-soft']) {
    derived['--revka-signal-selected-soft'] = `color-mix(in srgb, ${selected} 18%, transparent)`;
  }
  if (selected && !tokens['--revka-hover-surface']) {
    derived['--revka-hover-surface'] = `color-mix(in srgb, ${selected} 10%, var(--revka-bg-surface))`;
  }
  if (success && !tokens['--revka-status-success']) {
    derived['--revka-status-success'] = success;
  }
  if (success && !tokens['--revka-status-ok']) {
    derived['--revka-status-ok'] = success;
  }
  if (tokens['--revka-status-danger'] && !tokens['--revka-status-error']) {
    derived['--revka-status-error'] = tokens['--revka-status-danger'];
  }

  return derived;
}

function skinVars(
  skin: SkinSummary | null,
  scheme: 'dark' | 'light' | 'oled',
): Record<string, string> {
  const mode = modeForScheme(skin, scheme);
  const authoredTokens = mode?.tokens ?? {};
  const tokens = {
    ...deriveSkinTokenVars(authoredTokens),
    ...authoredTokens,
  };
  const assets = mode?.assets ?? {};
  const vars: Record<string, string> = {
    ...tokens,
    ...bridgePcVars(tokens),
    ...skinAssetVars(skin, assets),
  };
  return vars;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [stored] = useState(loadStored);
  const [theme, setThemeState] = useState<ThemeMode>(stored.theme);
  const [accent, setAccentState] = useState<AccentColor>(stored.accent);
  const [colorTheme, setColorThemeState] = useState<ColorThemeId>(stored.colorTheme);
  const [uiFont, setUiFontState] = useState<UiFont>(stored.uiFont);
  const [monoFont, setMonoFontState] = useState<MonoFont>(stored.monoFont);
  const [uiFontSize, setUiFontSizeState] = useState<number>(stored.uiFontSize);
  const [monoFontSize, setMonoFontSizeState] = useState<number>(stored.monoFontSize);
  const [activeSkinId, setActiveSkinIdState] = useState<string | null>(stored.activeSkinId);
  const [installedSkins, setInstalledSkins] = useState<SkinSummary[]>([]);
  const [skinsLoading, setSkinsLoading] = useState(false);

  const persist = useCallback((s: ThemeSettings) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      theme: s.theme,
      accent: s.accent,
      colorTheme: s.colorTheme,
      uiFont: s.uiFont,
      monoFont: s.monoFont,
      uiFontSize: s.uiFontSize,
      monoFontSize: s.monoFontSize,
      activeSkinId: s.activeSkinId,
    }));
  }, []);

  const applyAll = useCallback((s: ThemeSettings) => {
    const resolvedId = resolveColorTheme(s.theme, s.colorTheme);
    const ct = colorThemeMap[resolvedId];
    const themeVars = ct?.vars ?? colorThemeMap[DEFAULT_DARK_THEME].vars;
    const scheme = resolveThemeScheme(s.theme, s.colorTheme);
    const activeSkin = installedSkins.find((skin) => skin.id === s.activeSkinId) ?? null;
    const activeSkinVars = skinVars(activeSkin, scheme);
    document.documentElement.dataset.revkaSkin = activeSkin?.id ?? s.activeSkinId ?? 'none';
    clearStaleSkinVars(activeSkinVars);
    // Color theme provides base + its own accent. User accent overrides on top.
    applyVars({
      ...themeVars,
      ...accents[s.accent],
      ...fontVars(s.uiFont, s.monoFont, s.uiFontSize, s.monoFontSize),
      ...activeSkinVars,
    });
  }, [installedSkins]);

  const setTheme = useCallback((t: ThemeMode) => {
    setThemeState(t);
    const next: ThemeSettings = { theme: t, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId };
    applyAll(next);
    persist(next);
  }, [accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId, applyAll, persist]);

  const setAccent = useCallback((a: AccentColor) => {
    setAccentState(a);
    const next: ThemeSettings = { theme, accent: a, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId };
    applyAll(next);
    persist(next);
  }, [theme, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId, applyAll, persist]);

  const setColorTheme = useCallback((c: ColorThemeId) => {
    setColorThemeState(c);
    // Auto-adjust theme mode to match the color theme's scheme
    const ct = colorThemeMap[c];
    let newMode = theme;
    if (ct && theme !== 'system') {
      if (c === 'oled-black') {
        newMode = 'oled';
      } else {
        newMode = ct.scheme;
      }
      setThemeState(newMode);
    }
    const next: ThemeSettings = { theme: newMode, accent, colorTheme: c, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId };
    applyAll(next);
    persist(next);
  }, [theme, accent, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId, applyAll, persist]);

  const setUiFont = useCallback((f: UiFont) => {
    setUiFontState(f);
    loadUiFont(f);
    const next: ThemeSettings = { theme, accent, colorTheme, uiFont: f, monoFont, uiFontSize, monoFontSize, activeSkinId };
    applyAll(next);
    persist(next);
  }, [theme, accent, colorTheme, activeSkinId, applyAll, persist, monoFont, uiFontSize, monoFontSize]);

  const setMonoFont = useCallback((f: MonoFont) => {
    setMonoFontState(f);
    loadMonoFont(f);
    const next: ThemeSettings = { theme, accent, colorTheme, uiFont, monoFont: f, uiFontSize, monoFontSize, activeSkinId };
    applyAll(next);
    persist(next);
  }, [theme, accent, colorTheme, activeSkinId, applyAll, persist, uiFont, uiFontSize, monoFontSize]);

  const setUiFontSize = useCallback((size: number) => {
    const clamped = Math.min(20, Math.max(12, size));
    setUiFontSizeState(clamped);
    const next: ThemeSettings = { theme, accent, colorTheme, uiFont, monoFont, uiFontSize: clamped, monoFontSize, activeSkinId };
    applyAll(next);
    persist(next);
  }, [theme, accent, colorTheme, activeSkinId, applyAll, persist, uiFont, monoFont, monoFontSize]);

  const setMonoFontSize = useCallback((size: number) => {
    const clamped = Math.min(20, Math.max(12, size));
    setMonoFontSizeState(clamped);
    const next: ThemeSettings = { theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize: clamped, activeSkinId };
    applyAll(next);
    persist(next);
  }, [theme, accent, colorTheme, activeSkinId, applyAll, persist, uiFont, monoFont, uiFontSize]);

  const setSkin = useCallback((id: string | null) => {
    const normalized = id && installedSkins.some((skin) => skin.id === id) ? id : null;
    setActiveSkinIdState(normalized);
    const next: ThemeSettings = { theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId: normalized };
    applyAll(next);
    persist(next);
  }, [theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, installedSkins, applyAll, persist]);

  const refreshSkins = useCallback(async () => {
    if (!isAuthenticated) {
      return;
    }
    setSkinsLoading(true);
    try {
      const skins = await apiGetSkins();
      setInstalledSkins(skins);
      if (activeSkinId && !skins.some((skin) => skin.id === activeSkinId)) {
        setActiveSkinIdState(null);
        const next: ThemeSettings = { theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId: null };
        applyAll(next);
        persist(next);
      }
    } finally {
      setSkinsLoading(false);
    }
  }, [isAuthenticated, activeSkinId, theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, applyAll, persist]);

  const importSkinZip = useCallback(async (file: File) => {
    const skin = await apiImportSkinZip(file);
    setInstalledSkins((current) => {
      const without = current.filter((item) => item.id !== skin.id);
      return [...without, skin].sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
    });
    return skin;
  }, []);

  const deleteSkin = useCallback(async (id: string) => {
    await apiDeleteSkin(id);
    setInstalledSkins((current) => current.filter((skin) => skin.id !== id));
    if (activeSkinId === id) {
      setActiveSkinIdState(null);
      const next: ThemeSettings = { theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId: null };
      applyAll(next);
      persist(next);
    }
  }, [activeSkinId, theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, applyAll, persist]);

  useEffect(() => {
    loadUiFont(uiFont);
    loadMonoFont(monoFont);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    applyAll({ theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId });
  }, [theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId, applyAll]);

  useEffect(() => {
    if (authLoading || !isAuthenticated) return;
    void refreshSkins().catch(() => {
      // The pairing screen may render before API auth is available.
    });
  }, [authLoading, isAuthenticated, refreshSkins]);

  useEffect(() => {
    if (theme !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const handler = () => applyAll({ theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId });
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [theme, accent, colorTheme, applyAll, uiFont, monoFont, uiFontSize, monoFontSize, activeSkinId]);

  const resolvedTheme = resolveThemeScheme(theme, colorTheme);
  const activeSkin = installedSkins.find((skin) => skin.id === activeSkinId) ?? null;
  const activeSkinName = activeSkin?.name ?? null;

  const getSkinAsset = useCallback((slot: SkinAssetSlot): string | null => {
    const mode = modeForScheme(activeSkin, resolvedTheme);
    const asset = mode?.assets?.[slot];
    return activeSkin && asset ? skinAssetPath(activeSkin.id, asset) : null;
  }, [activeSkin, resolvedTheme]);

  const value: ThemeContextValue = {
    theme, accent, colorTheme, uiFont, monoFont, uiFontSize, monoFontSize,
    resolvedTheme,
    activeSkinId,
    activeSkinName,
    installedSkins,
    skinsLoading,
    setTheme,
    setAccent,
    setColorTheme,
    setUiFont,
    setMonoFont,
    setUiFontSize,
    setMonoFontSize,
    refreshSkins,
    setSkin,
    importSkinZip,
    deleteSkin,
    getSkinAsset,
  };

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
