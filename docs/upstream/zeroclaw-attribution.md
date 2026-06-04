# ZeroClaw Upstream Attribution

**Maintained by:** Revka maintainers
**Status:** Authoritative — this is the consolidated reference for Revka's relationship with the ZeroClaw upstream.

---

## At a glance

- **Upstream project:** [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw)
- **Upstream license:** Dual-licensed under MIT or Apache License 2.0
- **Upstream copyright holder:** ZeroClaw Labs (`Copyright (c) 2025 ZeroClaw Labs`)
- **Revka's relationship:** Independent fork, dual-licensed under MIT or Apache 2.0. Revka is not affiliated with, endorsed by, or sponsored by ZeroClaw Labs.
- **Required upstream files preserved in this repo:** `LICENSE-MIT`, `LICENSE-APACHE`, `NOTICE` (with verbatim upstream attribution block).

If you only need the legal text, read [`NOTICE`](../../NOTICE) at the repository root and `LICENSE-MIT` / `LICENSE-APACHE`. This document explains the *why* and the *what* alongside.

---

## What Revka inherited from ZeroClaw

Revka's Rust runtime is a fork of ZeroClaw at the commit it diverged from. The following structural elements trace directly to the upstream codebase and are covered by ZeroClaw Labs' copyright until Revka-specific modifications were applied:

- The Rust agent loop (request / plan / act / reflect cycle).
- The `provider` / `channel` / `tool` trait architecture and the trait boundaries between them.
- The hardware peripheral layer (STM32 / Arduino / ESP32 / Pico / Aardvark backends).
- The `revka` CLI scaffolding (originally `zeroclaw` CLI).
- The audit primitives (Merkle hash chain over runtime events).
- The installer pattern and the source/prebuilt binary toggle.
- The dual-license + NOTICE structure itself.

Revka redistributes these components under MIT or Apache 2.0, preserves the upstream copyright in `LICENSE-MIT`, and preserves the verbatim upstream NOTICE attribution under "Upstream Attribution Notices" in `NOTICE`.

---

## What Revka adds beyond ZeroClaw

Revka is not a transparent rebrand of ZeroClaw — it adds substantial new subsystems, each authored by Revka contributors and carrying Revka's own copyright (`Copyright 2026 Kumiho Inc.`):

- **Kumiho graph-native memory substrate** — the persistent cognitive memory backend (`src/kumiho/`, the Kumiho MCP sidecar, the `[kumiho]` config block) replacing any ephemeral or file-based memory abstraction in upstream.
- **Operator** — a Python MCP workflow engine (`operator-mcp/`) for declarative multi-agent orchestration, DAG execution, and SOP runtime.
- **Embedded React dashboard** — the 18-route React/TypeScript Web UI baked into the Rust binary via `rust-embed`, served at `http://127.0.0.1:42617`. Lives under `web/`.
- **Trust scoring** — per-agent and per-team trust evolution across runs.
- **A2A interoperability** — agent-to-agent protocol and bridges.
- **ClawHub marketplace** — skill, agent, and workflow distribution.
- **Multi-node distribution** — gateway/daemon/worker topology and remote pairing.
- **Canvas** — real-time HTML/CSS/JS sandbox with WebSocket-driven rendering.
- **Cost governance** — per-model token accounting, spend caps, daily budget enforcement.
- **Cron scheduling** — managed scheduled jobs with run history.
- **Device pairing** — issued-token enrolment for hardware and remote devices.
- **OpenClaw migration** — `revka migrate` command for importing AIEOS / OpenClaw identity data from the separate OpenClaw TypeScript platform.

Files added or substantially rewritten by Revka carry Revka's copyright; files inherited mostly unchanged from ZeroClaw still trace to ZeroClaw Labs' copyright. The git history at <https://github.com/KumihoIO/Revka> is the authoritative record of what each commit changed (Apache License 2.0 §4(b)).

---

## Compliance checklist for forks of Revka

If you are forking Revka (a fork of a fork), you inherit obligations to **both** ZeroClaw Labs (the original upstream) and Kumiho Inc. (Revka). To stay compliant:

1. **Preserve `LICENSE-MIT` exactly.** Both `Copyright (c) 2025 ZeroClaw Labs` and `Copyright (c) 2026 Kumiho Inc.` lines must remain. You may add your own copyright line *after* these — never replace them.
2. **Preserve `LICENSE-APACHE`.** Apache 2.0 license text is unchanged from upstream and must remain unchanged.
3. **Preserve `NOTICE`** including the "Upstream Attribution Notices" block (the verbatim ZeroClaw attribution) and the "Modifications from Upstream" section. You may add your own modifications section beneath these — never remove or reword the upstream blocks.
4. **State the fork relationship.** Your README must clearly identify your project as a fork of Revka (and transitively of ZeroClaw). Link to both upstream repositories.
5. **Honour both trademarks.** "ZeroClaw" and the ZeroClaw logo are trademarks of ZeroClaw Labs. "Revka" is governed by [docs/maintainers/trademark.md](../maintainers/trademark.md) — community norms apply, no registered trademark currently. Do not use either name in a way that could imply affiliation, endorsement, or impersonation.
6. **Record your modifications.** Apache 2.0 §4(b) requires modified files to carry prominent notices of change. Git history satisfies this; supplement it in your own `NOTICE` if your fork makes substantial structural changes.
7. **Carry forward third-party attributions.** The Verifiable Intent attribution block in `NOTICE` (originally inherited from ZeroClaw) must remain intact for any code under `src/verifiable_intent/`.

If you publish a derivative under a distinct name, credit Revka **and** ZeroClaw in your README. The dual-license model gives you flexibility on how you redistribute, but the attribution chain is non-negotiable.

---

## Where each piece lives

| File | Purpose |
|---|---|
| [`LICENSE-MIT`](../../LICENSE-MIT) | MIT license text with both ZeroClaw Labs and Kumiho Inc. copyright lines. |
| [`LICENSE-APACHE`](../../LICENSE-APACHE) | Verbatim Apache License 2.0 text. |
| [`NOTICE`](../../NOTICE) | Revka's NOTICE plus the preserved upstream ZeroClaw attribution block (Apache 2.0 §4(c)) and the modifications notice (§4(b)). |
| [`docs/maintainers/trademark.md`](../maintainers/trademark.md) | Naming and attribution norms for the Revka project, including the ZeroClaw trademark acknowledgement. |
| [`docs/contributing/cla.md`](../contributing/cla.md) | Contributor License Agreement under which Revka contributors grant rights under both MIT and Apache 2.0. |
| This file | Plain-language overview of Revka's relationship with the ZeroClaw upstream and a fork-compliance checklist. |

---

## Reporting attribution problems

If you believe Revka has failed to honour an upstream attribution obligation — missing copyright line, missing notice block, incorrect modification record — please open an issue at <https://github.com/KumihoIO/Revka/issues> with the specific clause and the current state of the relevant file. Attribution issues are treated as high priority and will be addressed in the next release window at the latest.

---

*This document is non-normative. The legal terms are governed by `LICENSE-MIT`, `LICENSE-APACHE`, and `NOTICE` at the repository root.*
