# Revka 文档（简体中文）

这是 Revka 文档系统的中文入口页。

> Revka 的 Rust 核心运行时是 [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) 的 fork；完整署名见根目录 [`NOTICE`](../../../NOTICE) 与 [`docs/upstream/zeroclaw-attribution.md`](../../upstream/zeroclaw-attribution.md)。

最后对齐：**April 27, 2026**。

> 说明：命令、配置键、API 路径保持英文；实现细节以英文文档为准。未翻译的页面会回退到英文原文并标注 *(英文)*。

其他语言：[English](../../README.md) · [한국어](../ko/README.md) · [Tiếng Việt](../vi/README.md)。

---

## 按角色入门

- **第一次接触 Revka？** → [setup-guides/one-click-bootstrap.zh-CN.md](setup-guides/one-click-bootstrap.zh-CN.md)
- **硬件 / 嵌入式？** → [hardware/README.zh-CN.md](hardware/README.zh-CN.md)
- **生产环境运维？** → [ops/README.zh-CN.md](ops/README.zh-CN.md)
- **通过 API / MCP 集成？** → [reference/README.zh-CN.md](reference/README.zh-CN.md) + [contributing/README.zh-CN.md](contributing/README.zh-CN.md)
- **PR 评审 / 合入？** → [contributing/pr-workflow.zh-CN.md](contributing/pr-workflow.zh-CN.md) + [contributing/reviewer-playbook.zh-CN.md](contributing/reviewer-playbook.zh-CN.md)
- **完整目录** → [SUMMARY.md](SUMMARY.md)

## 快速入口

| 我想要… | 建议阅读 |
|---|---|
| 快速安装并运行 | [../../../README.md](../../../README.md#install) *(英文)* |
| 一键安装与初始化 | [setup-guides/one-click-bootstrap.zh-CN.md](setup-guides/one-click-bootstrap.zh-CN.md) |
| 安装 Kumiho 内存 sidecar | [../../setup-guides/kumiho-operator-setup.md](../../setup-guides/kumiho-operator-setup.md) *(英文)* |
| macOS 平台更新与卸载 | [setup-guides/macos-update-uninstall.zh-CN.md](setup-guides/macos-update-uninstall.zh-CN.md) |
| 按任务找命令 | [reference/cli/commands-reference.zh-CN.md](reference/cli/commands-reference.zh-CN.md) |
| 快速查看配置默认值与关键项 | [reference/api/config-reference.zh-CN.md](reference/api/config-reference.zh-CN.md) |
| 接入自定义 Provider / endpoint | [contributing/custom-providers.zh-CN.md](contributing/custom-providers.zh-CN.md) |
| 配置 Z.AI / GLM Provider | [setup-guides/zai-glm-setup.zh-CN.md](setup-guides/zai-glm-setup.zh-CN.md) |
| Kumiho 图原生认知记忆集成 | [../../contributing/kumiho-memory-integration.md](../../contributing/kumiho-memory-integration.md) *(英文)* |
| 进行日常运维（runbook） | [ops/operations-runbook.zh-CN.md](ops/operations-runbook.zh-CN.md) |
| 快速排查安装/运行/通道问题 | [ops/troubleshooting.zh-CN.md](ops/troubleshooting.zh-CN.md) |
| Matrix 加密房间配置与诊断 | [security/matrix-e2ee-guide.zh-CN.md](security/matrix-e2ee-guide.zh-CN.md) |

## Revka 的关键特征

- **以记忆为本的 Rust 代理运行时** — 每个会话、计划、技能与信任分都常驻在 Kumiho 图谱中。
- **单一二进制** — 网关、daemon、内嵌 React 仪表盘、MCP sidecar 与 CLI 同打包于一个静态二进制。
- **声明式编排** — Operator 以 YAML 工作流驱动多代理。
- **一级硬件支持** — STM32、Arduino、ESP32、Pico、Aardvark I²C/SPI 全部以代理工具表面暴露。
- **18 路由 Web 仪表盘** — `http://127.0.0.1:42617` 覆盖 Orchestration / Operations / Inspection 三块。
- **信任评分 + ClawHub 市场** — 代理在多次执行间累积信任，并通过内容寻址注册表共享技能。

---

## 安装与上手

- [setup-guides/README.zh-CN.md](setup-guides/README.zh-CN.md) — 安装索引
- [setup-guides/one-click-bootstrap.zh-CN.md](setup-guides/one-click-bootstrap.zh-CN.md) — 一键安装
- [../../setup-guides/kumiho-operator-setup.md](../../setup-guides/kumiho-operator-setup.md) — Kumiho 内存 sidecar *(英文)*
- [setup-guides/macos-update-uninstall.zh-CN.md](setup-guides/macos-update-uninstall.zh-CN.md) — macOS 生命周期
- [../../setup-guides/windows-setup.md](../../setup-guides/windows-setup.md) — Windows 安装 *(英文)*
- [../../setup-guides/dashboard-dev.md](../../setup-guides/dashboard-dev.md) — 本地运行 `web/` 仪表盘 *(英文)*
- [setup-guides/nextcloud-talk-setup.zh-CN.md](setup-guides/nextcloud-talk-setup.zh-CN.md) — Nextcloud Talk
- [setup-guides/mattermost-setup.zh-CN.md](setup-guides/mattermost-setup.zh-CN.md) — Mattermost
- [setup-guides/zai-glm-setup.zh-CN.md](setup-guides/zai-glm-setup.zh-CN.md) — Z.AI / GLM Provider
- [../../browser-setup.md](../../browser-setup.md) — 浏览器通道 / VNC *(英文)*

## 日常使用

- [reference/cli/commands-reference.zh-CN.md](reference/cli/commands-reference.zh-CN.md) — CLI 命令索引
- [reference/api/config-reference.zh-CN.md](reference/api/config-reference.zh-CN.md) — 配置项与默认值
- [reference/api/providers-reference.zh-CN.md](reference/api/providers-reference.zh-CN.md) — Provider ID 与凭证
- [reference/api/channels-reference.zh-CN.md](reference/api/channels-reference.zh-CN.md) — 通道能力与配置路径
- [reference/sop/observability.zh-CN.md](reference/sop/observability.zh-CN.md) — SOP 运行可观测性

## 集成

- [../../contributing/kumiho-memory-integration.md](../../contributing/kumiho-memory-integration.md) — Kumiho 图原生认知记忆集成 *(英文)*
- [contributing/custom-providers.zh-CN.md](contributing/custom-providers.zh-CN.md) — 自定义 Provider
- [contributing/extension-examples.zh-CN.md](contributing/extension-examples.zh-CN.md) — 扩展示例
- [contributing/adding-boards-and-tools.zh-CN.md](contributing/adding-boards-and-tools.zh-CN.md) — 新增硬件与工具

## 运维与部署

- [ops/README.zh-CN.md](ops/README.zh-CN.md) — 运维索引
- [ops/operations-runbook.zh-CN.md](ops/operations-runbook.zh-CN.md) — 运维 runbook
- [ops/troubleshooting.zh-CN.md](ops/troubleshooting.zh-CN.md) — 故障特征与恢复
- [ops/network-deployment.zh-CN.md](ops/network-deployment.zh-CN.md) — Raspberry Pi / 局域网部署
- [ops/proxy-agent-playbook.zh-CN.md](ops/proxy-agent-playbook.zh-CN.md) — 代理模式
- [ops/resource-limits.zh-CN.md](ops/resource-limits.zh-CN.md) — 运行时资源控制
- [contributing/release-process.zh-CN.md](contributing/release-process.zh-CN.md) — 发布流程

## 安全

- [security/README.zh-CN.md](security/README.zh-CN.md) — 安全索引
- [security/agnostic-security.zh-CN.md](security/agnostic-security.zh-CN.md) — Provider 无关的安全模型
- [security/frictionless-security.zh-CN.md](security/frictionless-security.zh-CN.md) — 无摩擦默认值
- [security/sandboxing.zh-CN.md](security/sandboxing.zh-CN.md) — Seatbelt / Landlock / Firejail / Bubblewrap
- [security/audit-logging.zh-CN.md](security/audit-logging.zh-CN.md) — Merkle 链审计日志
- [security/matrix-e2ee-guide.zh-CN.md](security/matrix-e2ee-guide.zh-CN.md) — Matrix E2EE
- [security/security-roadmap.zh-CN.md](security/security-roadmap.zh-CN.md) — 安全路线图

## 硬件与外设

- [hardware/README.zh-CN.md](hardware/README.zh-CN.md) — 硬件索引
- [hardware/hardware-peripherals-design.zh-CN.md](hardware/hardware-peripherals-design.zh-CN.md) — 外设架构
- [hardware/nucleo-setup.zh-CN.md](hardware/nucleo-setup.zh-CN.md) — STM32 Nucleo
- [hardware/arduino-uno-q-setup.zh-CN.md](hardware/arduino-uno-q-setup.zh-CN.md) — Arduino Uno Q
- [hardware/android-setup.zh-CN.md](hardware/android-setup.zh-CN.md) — Android / Termux

## 贡献

- [../../../CONTRIBUTING.md](../../../CONTRIBUTING.md) *(英文)*
- [contributing/README.zh-CN.md](contributing/README.zh-CN.md) — 贡献者索引
- [contributing/pr-workflow.zh-CN.md](contributing/pr-workflow.zh-CN.md) — PR 治理与评审车道
- [contributing/reviewer-playbook.zh-CN.md](contributing/reviewer-playbook.zh-CN.md) — Reviewer 指南
- [contributing/ci-map.zh-CN.md](contributing/ci-map.zh-CN.md) — CI 工作流地图
- [contributing/cla.zh-CN.md](contributing/cla.zh-CN.md) — Contributor License Agreement

## 架构与参考

- [../../architecture/adr-004-tool-shared-state-ownership.md](../../architecture/adr-004-tool-shared-state-ownership.md) *(英文)*
- [../../architecture/adr-005-operator-liveness-and-rust-migration.md](../../architecture/adr-005-operator-liveness-and-rust-migration.md) *(英文)*
- [reference/sop/connectivity.zh-CN.md](reference/sop/connectivity.zh-CN.md) — 连接 SOP
- [reference/sop/observability.zh-CN.md](reference/sop/observability.zh-CN.md) — 可观测性 SOP

## 维护者笔记

- [maintainers/README.zh-CN.md](maintainers/README.zh-CN.md) — 维护者索引
- [maintainers/structure-README.zh-CN.md](maintainers/structure-README.zh-CN.md) — 文档结构图
- [maintainers/docs-inventory.zh-CN.md](maintainers/docs-inventory.zh-CN.md) — 文档清单
- [maintainers/i18n-coverage.zh-CN.md](maintainers/i18n-coverage.zh-CN.md) — i18n 覆盖度
- [maintainers/trademark.zh-CN.md](maintainers/trademark.zh-CN.md) — 命名与署名规范

## 许可与上游署名

- [`../../../NOTICE`](../../../NOTICE) — 根目录 NOTICE，按 Apache 2.0 §4(c) 保留 ZeroClaw 上游署名
- [`../../../LICENSE-MIT`](../../../LICENSE-MIT)、[`../../../LICENSE-APACHE`](../../../LICENSE-APACHE) — 双重许可证文本
- [docs/upstream/zeroclaw-attribution.md](../../upstream/zeroclaw-attribution.md) — Revka 从 ZeroClaw 继承的内容与 fork 合规清单 *(英文)*
- [maintainers/trademark.zh-CN.md](maintainers/trademark.zh-CN.md) — Revka 命名规范与 ZeroClaw 商标致谢

## 其他语言

- [English](../../README.md)
- [한국어](../ko/README.md)
- [Tiếng Việt](../vi/README.md)
