// Runtime base path injected by the Rust gateway into index.html.
// Allows the SPA to work under a reverse-proxy path prefix.
// When running inside Tauri, the frontend is served from disk so basePath is
// empty and API calls target the gateway URL directly.

import { isTauri, tauriGatewayUrl } from './tauri';

declare global {
  interface Window {
    __CONSTRUCT_BASE__?: string;
  }
}

/** Gateway path prefix (e.g. "/construct"), or empty string when served at root. */
export const basePath: string = isTauri()
  ? ''
  : (window.__CONSTRUCT_BASE__ ?? '').replace(/\/+$/, '');

/** Full origin for API requests. Empty when served by the gateway (same-origin). */
export const apiOrigin: string = isTauri() ? tauriGatewayUrl() : '';

const appAssetPrefix =
  import.meta.env.DEV && !isTauri() ? '' : `${basePath}/_app`;

export function appAssetPath(asset: string): string {
  const normalizedAsset = asset.replace(/^\/+/, '');
  const prefix = appAssetPrefix.replace(/\/+$/, '');
  return prefix ? `${prefix}/${normalizedAsset}` : `/${normalizedAsset}`;
}

export function skinAssetPath(skinId: string, assetPath: string): string {
  const normalizedSkinId = encodeURIComponent(skinId);
  const normalizedAsset = assetPath
    .replace(/^\/+/, '')
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/');
  return `${apiOrigin}${basePath}/api/skins/${normalizedSkinId}/assets/${normalizedAsset}`;
}
