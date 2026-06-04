# Dashboard Development

Revka's dashboard is a React + TypeScript + Tailwind + Vite app in
[`web/`](../../web). The gateway and frontend are now developed independently
by default:

- `cargo build` is Rust-only and does not run `npm ci` or `npm run build`
- Vite serves the frontend during local UI work
- release binaries can still embed `web/dist/` as a dashboard fallback
- the gateway can serve a filesystem build via `REVKA_WEB_ROOT` or
  `gateway.web_root`

The landing entry point lives at [`web/src/pages/Landing.tsx`](../../web/src/pages/Landing.tsx).
Operational pages live under [`web/src/revka/pages/`](../../web/src/revka/pages/),
with shell/layout/navigation under
[`web/src/revka/components/layout/`](../../web/src/revka/components/layout/).
Routing is defined in [`web/src/App.tsx`](../../web/src/App.tsx).

## Local Rust Development

Rust contributors do not need Node.js unless they are editing the frontend.

```powershell
cargo run -- gateway start
```

Open:

- Gateway-served dashboard, if a filesystem or embedded build is available:
  `http://127.0.0.1:42617/`
- API health: `http://127.0.0.1:42617/health`

If no dashboard build is available, the gateway returns a dashboard-unavailable
response for UI routes while API, WebSocket, pairing, health, and webhook routes
continue to run normally.

## Frontend Dev Workflow

Terminal 1:

```powershell
cargo run -- gateway start
```

Terminal 2:

```powershell
cd web
npm ci
npm run dev
```

Open:

- Vite dev UI: `http://127.0.0.1:5173`

Vite proxies these gateway surfaces to `http://127.0.0.1:42617` by default:

- `/api`
- `/ws`
- `/pair`
- `/health`
- `/admin`

Set `REVKA_GATEWAY_URL=http://host:port` before `npm run dev` if your
gateway runs elsewhere.

## Filesystem Dashboard Build

Use a filesystem web root when you want the Rust gateway to serve a local build
without embedding it into the binary.

```powershell
cd web
npm ci
npm run build

cd ..
$env:REVKA_WEB_ROOT = "$PWD/web/dist"
cargo run -- gateway start
```

Equivalent config:

```toml
[gateway]
web_root = "/absolute/path/to/revka/web/dist"
```

Resolution order for dashboard assets:

1. `REVKA_WEB_ROOT`, when set and non-empty
2. `gateway.web_root`, when set
3. embedded `web/dist` via `rust-embed`
4. dashboard unavailable response

Filesystem serving canonicalizes the configured root and rejects path traversal.
Hashed assets under `assets/` receive immutable cache headers; `index.html`
receives `no-cache`.

## Embedded Release Fallback

Official release builds should build `web/dist` before compiling the release
binary:

```powershell
cd web
npm ci
npm run build

cd ..
cargo build --release --locked
```

`build.rs` no longer builds the frontend by default. For compatibility with the
old cargo-triggered flow, set:

```powershell
$env:REVKA_BUILD_WEB = "1"
cargo build
```

That opt-in path runs the existing npm install/build attempt from `build.rs`.
The default path keeps local Rust iteration Node-free.

## Desktop App Workflow

The Tauri shell points at the gateway-served dashboard by default:

- production/default: `http://127.0.0.1:42617/_app/`

During frontend development, use the Vite URL instead when you want hot reload:

- development: `http://127.0.0.1:5173`

The practical loop is:

1. Run the gateway
2. Run Vite for UI work or build `web/dist` for embedded/filesystem serving
3. Launch the Tauri shell against the desired dashboard URL

## Dashboard Shell Ownership

If you are changing navigation, the main ownership points are:

- [`web/src/revka/components/layout/Layout.tsx`](../../web/src/revka/components/layout/Layout.tsx): shell frame, content offset, mobile drawer state
- [`web/src/revka/components/layout/Sidebar.tsx`](../../web/src/revka/components/layout/Sidebar.tsx): sidebar UI and nav rendering
- [`web/src/revka/components/layout/Header.tsx`](../../web/src/revka/components/layout/Header.tsx): top bar actions and current-surface context
- [`web/src/revka/components/layout/revka-navigation.ts`](../../web/src/revka/components/layout/revka-navigation.ts): canonical nav sections and route metadata
- [`web/src/revka/pages/Dashboard.tsx`](../../web/src/revka/pages/Dashboard.tsx): dashboard workspace and skin hero surface
- [`web/src/revka/pages/Skins.tsx`](../../web/src/revka/pages/Skins.tsx): skin ZIP import, preview, activation, and deletion
- [`web/src/index.css`](../../web/src/index.css): global frontend chrome
- [`web/src/revka/styles/theme.css`](../../web/src/revka/styles/theme.css): Revka shell/theme tokens

Route declarations live in [`web/src/App.tsx`](../../web/src/App.tsx). The
legacy `/memory-auditor` URL redirects to `/memory`.
