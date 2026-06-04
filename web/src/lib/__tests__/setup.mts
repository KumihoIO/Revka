// Test setup: install browser-shaped globals + a tiny ESM loader hook so
// modules that read `window` / `import.meta.env` at module-init can be
// imported under `tsx --test`.
//
// Imported for side effects only. Must be the FIRST import in any test
// file that pulls in `../api` (which transitively loads `basePath.ts`
// and `tauri.ts`). ESM import statements are hoisted but evaluated in
// declaration order, so a side-effect import at the top of the test
// file runs before any sibling import.

import { register } from 'node:module';
import { pathToFileURL } from 'node:url';

// Replace Node 25's built-in `localStorage` (which requires the
// `--localstorage-file=PATH` flag to actually work) with a plain Map-
// backed stub so the auth module's `setItem` / `getItem` round-trip.
const tokenStore = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  enumerable: true,
  value: {
    getItem: (k: string) => tokenStore.get(k) ?? null,
    setItem: (k: string, v: string) => { tokenStore.set(k, v); },
    removeItem: (k: string) => { tokenStore.delete(k); },
  },
});

// `tauri.ts` checks `'__TAURI__' in window` and `basePath.ts` reads
// `window.__REVKA_BASE__`, so the object must exist.
if (!(globalThis as any).window) {
  (globalThis as any).window = {
    __REVKA_BASE__: '',
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
  };
}

// Register a loader hook that rewrites `basePath.ts` source to remove the
// `import.meta.env.DEV` reference (Vite-injected, unavailable under tsx).
// The hook code lives inline as a data URL so we don't need a separate
// loader file.
const LOADER = `
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (url.endsWith('/src/lib/basePath.ts') && result.source) {
    // Vite-only token: \`import.meta.env.DEV\` → \`false\` for the test env.
    // Patches the already-transpiled JS so we don't have to re-run TS.
    const src = typeof result.source === 'string'
      ? result.source
      : Buffer.from(result.source).toString('utf8');
    return { ...result, source: src.replace(/import\\.meta\\.env\\.DEV/g, 'false') };
  }
  return result;
}
`;
register('data:text/javascript;base64,' + Buffer.from(LOADER).toString('base64'), pathToFileURL('./'));

export {};
