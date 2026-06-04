import { Languages } from 'lucide-react';
import { AVAILABLE_LOCALES, useT, type Locale } from '@/revka/hooks/useT';

export default function LanguageSwitcher() {
  const { locale, setLocale } = useT();
  return (
    <label className="revka-status-pill relative cursor-pointer" title="Language">
      <Languages className="h-3.5 w-3.5" />
      <select
        aria-label="Language"
        value={locale}
        onChange={(e) => setLocale(e.target.value as Locale)}
        className="bg-transparent text-xs font-medium focus:outline-none"
        style={{ color: 'inherit' }}
      >
        {AVAILABLE_LOCALES.map((l) => (
          <option key={l.code} value={l.code} style={{ color: '#111' }}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  );
}
