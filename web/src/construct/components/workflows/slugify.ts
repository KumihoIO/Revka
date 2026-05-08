/**
 * Shared slug helpers for workflow editor surfaces.
 *
 * Imported by StepConfigPanel (step IDs), NewPoolAgentModal (agent names),
 * and NewAuthProfileModal (profile names) so all three stay in sync — a
 * change to slug rules in one place propagates everywhere.
 */

/** ASCII-only slug. Strips diacritics, lowercases, collapses any
 *  non-`[a-z0-9]` runs to single `-`, trims edges, falls back to a default
 *  if empty, caps at 64 chars. */
export function slugify(input: string, fallback: string = 'item'): string {
  const normalized = (input ?? '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '');
  const slug = normalized
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/g, '');
  return slug || fallback;
}

/** Append `-2`, `-3`, … until a slug doesn't collide with `existing`. */
export function uniqueSlug(slug: string, existing: Iterable<string>): string {
  const taken = new Set(existing);
  if (!taken.has(slug)) return slug;
  for (let i = 2; i < 1000; i++) {
    const candidate = `${slug}-${i}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${slug}-${Date.now()}`;
}
