import { useCallback, useMemo } from 'react';
import { useLocaleContext } from '@/App';
import { tLocale, tplLocale, type Locale } from '@/lib/i18n';

export type { Locale };

export const AVAILABLE_LOCALES: { code: Locale; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'zh', label: '中文' },
  { code: 'tr', label: 'Türkçe' },
  { code: 'ko', label: '한국어' },
];

export function useT(): {
  t: (key: string) => string;
  tpl: (key: string, vars?: Record<string, string | number>) => string;
  locale: Locale;
  setLocale: (locale: Locale) => void;
} {
  const { locale, setAppLocale } = useLocaleContext();
  const narrowed = (AVAILABLE_LOCALES.find((l) => l.code === locale)?.code ?? 'en') as Locale;

  const t = useCallback((key: string) => tLocale(key, narrowed), [narrowed]);
  const tpl = useCallback(
    (key: string, vars?: Record<string, string | number>) => tplLocale(key, narrowed, vars),
    [narrowed],
  );
  const setLocale = useCallback((next: Locale) => setAppLocale(next), [setAppLocale]);

  return useMemo(
    () => ({ locale: narrowed, t, tpl, setLocale }),
    [narrowed, t, tpl, setLocale],
  );
}
