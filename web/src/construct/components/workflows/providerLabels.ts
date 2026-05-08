/**
 * Shared provider label table for auth-profile surfaces.
 *
 * `AuthProfilePicker` uses this for group headings; `NewAuthProfileModal`
 * uses it as the source for the provider-name datalist autocomplete. Other
 * surfaces (e.g. side-panel auth chip) consume it via `providerLabel()`.
 */

/** Display labels for known auth providers. Falls through to the raw
 *  provider key for anything not listed — so a typo or new provider still
 *  renders, just unbranded. */
export const PROVIDER_LABELS: Record<string, string> = {
  'openai-codex': 'OpenAI Codex',
  'openai': 'OpenAI',
  'anthropic': 'Anthropic',
  'claude-code': 'Claude Code',
  'gmail': 'Gmail',
  'google': 'Google',
  'slack': 'Slack',
  'discord': 'Discord',
  'matrix': 'Matrix',
  'notion': 'Notion',
  'github': 'GitHub',
  'gitlab': 'GitLab',
  'linear': 'Linear',
  'jira': 'Jira',
};

export function providerLabel(p: string): string {
  return PROVIDER_LABELS[p] ?? p;
}
