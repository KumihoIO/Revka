const TOKEN_KEY = 'revka_token';
const OLD_TOKEN_KEY = 'revka_token';

// Migrate token from old key on first access
try {
  if (!localStorage.getItem(TOKEN_KEY)) {
    const old = localStorage.getItem(OLD_TOKEN_KEY);
    if (old) {
      localStorage.setItem(TOKEN_KEY, old);
      localStorage.removeItem(OLD_TOKEN_KEY);
    }
  }
} catch {
  // ignore
}

/**
 * Retrieve the stored authentication token.
 */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/**
 * Store an authentication token.
 */
export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // localStorage may be unavailable (e.g. in some private browsing modes)
  }
}

/**
 * Remove the stored authentication token.
 */
export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Ignore
  }
}

/**
 * Returns true if a token is currently stored.
 */
export function isAuthenticated(): boolean {
  const token = getToken();
  return token !== null && token.length > 0;
}
