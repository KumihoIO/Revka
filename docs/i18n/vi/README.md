# Tài liệu Revka (Tiếng Việt)

Đây là trang chủ tiếng Việt của hệ thống tài liệu Revka.

> Runtime Rust lõi của Revka là fork của [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw); xem [`NOTICE`](../../../NOTICE) để biết attribution đầy đủ và [`docs/upstream/zeroclaw-attribution.md`](../../upstream/zeroclaw-attribution.md) để hiểu mối quan hệ với upstream.

Đồng bộ lần cuối: **April 27, 2026**.

> Lưu ý: Tên lệnh, khóa cấu hình và đường dẫn API giữ nguyên tiếng Anh. Khi có sai khác, tài liệu tiếng Anh là bản gốc. Trang chưa có bản dịch sẽ trỏ về tài liệu gốc tiếng Anh và được đánh dấu *(tiếng Anh)*.

Ngôn ngữ khác: [English](../../README.md) · [한국어](../ko/README.md) · [简体中文](../zh-CN/README.md).

---

## Bắt đầu theo vai trò

- **Mới tiếp xúc Revka?** → [one-click-bootstrap.md](one-click-bootstrap.md)
- **Phần cứng / nhúng?** → [../../hardware/README.md](../../hardware/README.md) *(tiếng Anh)*
- **Vận hành sản phẩm?** → [../../ops/README.md](../../ops/README.md) *(tiếng Anh)* / [operations-runbook.md](operations-runbook.md)
- **Tích hợp qua API / MCP?** → [../../reference/README.md](../../reference/README.md) *(tiếng Anh)* + [../../contributing/README.md](../../contributing/README.md) *(tiếng Anh)*
- **Review / merge PR?** → [pr-workflow.md](pr-workflow.md) + [reviewer-playbook.md](reviewer-playbook.md)
- **Toàn bộ mục lục** → [SUMMARY.md](SUMMARY.md)

## Tra cứu nhanh

| Tôi muốn… | Xem tài liệu |
|---|---|
| Cài đặt và chạy nhanh | [README.md](../../../README.md#install) *(tiếng Anh)* |
| Cài đặt bằng một lệnh | [one-click-bootstrap.md](one-click-bootstrap.md) |
| Cài đặt sidecar bộ nhớ Kumiho | [../../setup-guides/kumiho-operator-setup.md](../../setup-guides/kumiho-operator-setup.md) *(tiếng Anh)* |
| Tìm lệnh theo tác vụ | [commands-reference.md](commands-reference.md) |
| Kiểm tra giá trị mặc định và khóa cấu hình | [config-reference.md](config-reference.md) |
| Kết nối provider / endpoint tùy chỉnh | [custom-providers.md](custom-providers.md) |
| Cấu hình Z.AI / GLM provider | [../../setup-guides/zai-glm-setup.md](../../setup-guides/zai-glm-setup.md) *(tiếng Anh)* |
| Tích hợp bộ nhớ nhận thức Kumiho graph-native | [../../contributing/kumiho-memory-integration.md](../../contributing/kumiho-memory-integration.md) *(tiếng Anh)* |
| Vận hành runtime (runbook ngày 2) | [operations-runbook.md](operations-runbook.md) |
| Khắc phục sự cố cài đặt/chạy/kênh | [troubleshooting.md](troubleshooting.md) |
| Cấu hình Matrix phòng mã hóa (E2EE) | [matrix-e2ee-guide.md](matrix-e2ee-guide.md) |
| Xem theo danh mục | [SUMMARY.md](SUMMARY.md) |

## Revka tạo nên Revka

- **Runtime Rust hướng-bộ-nhớ** — mọi session, kế hoạch, skill và điểm tin cậy đều được lưu trong đồ thị Kumiho.
- **Một binary duy nhất** — gateway, daemon, dashboard React nhúng, sidecar MCP và CLI cùng đóng gói tĩnh.
- **Điều phối khai báo** — Operator chạy workflow đa tác nhân định nghĩa bằng YAML.
- **Phần cứng cấp một** — STM32, Arduino, ESP32, Pico và Aardvark I²C/SPI hiển thị thành công cụ tác nhân.
- **Web dashboard 18 route** — `http://127.0.0.1:42617` cho Orchestration / Operations / Inspection.
- **Trust scoring + chợ ClawHub** — tác nhân tích lũy tin cậy qua các lần chạy và chia sẻ kỹ năng qua registry nội dung.

---

## Cài đặt & onboarding

- [one-click-bootstrap.md](one-click-bootstrap.md) — cài đặt một lệnh
- [../../setup-guides/kumiho-operator-setup.md](../../setup-guides/kumiho-operator-setup.md) — sidecar bộ nhớ Kumiho *(tiếng Anh)*
- [../../setup-guides/macos-update-uninstall.md](../../setup-guides/macos-update-uninstall.md) — vòng đời macOS *(tiếng Anh)*
- [../../setup-guides/windows-setup.md](../../setup-guides/windows-setup.md) — cài đặt Windows *(tiếng Anh)*
- [../../setup-guides/dashboard-dev.md](../../setup-guides/dashboard-dev.md) — chạy dashboard `web/` cục bộ *(tiếng Anh)*
- [../../setup-guides/nextcloud-talk-setup.md](../../setup-guides/nextcloud-talk-setup.md) — kênh Nextcloud Talk *(tiếng Anh)*
- [mattermost-setup.md](mattermost-setup.md) — kênh Mattermost
- [../../browser-setup.md](../../browser-setup.md) — kênh trình duyệt / VNC *(tiếng Anh)*

## Sử dụng hằng ngày

- [commands-reference.md](commands-reference.md) — bảng lệnh CLI
- [config-reference.md](config-reference.md) — khóa cấu hình và giá trị mặc định
- [providers-reference.md](providers-reference.md) — ID provider và biến môi trường
- [channels-reference.md](channels-reference.md) — khả năng kênh và đường dẫn cài đặt

## Tích hợp

- [../../contributing/kumiho-memory-integration.md](../../contributing/kumiho-memory-integration.md) — tích hợp bộ nhớ nhận thức Kumiho graph-native *(tiếng Anh)*
- [custom-providers.md](custom-providers.md) — provider tùy chỉnh
- [adding-boards-and-tools.md](adding-boards-and-tools.md) — thêm bo mạch và công cụ

## Vận hành & triển khai

- [operations-runbook.md](operations-runbook.md) — runbook ngày 2
- [troubleshooting.md](troubleshooting.md) — dấu hiệu lỗi và phục hồi
- [network-deployment.md](network-deployment.md) — Raspberry Pi / LAN
- [proxy-agent-playbook.md](proxy-agent-playbook.md) — chế độ proxy
- [resource-limits.md](resource-limits.md) — giới hạn tài nguyên
- [release-process.md](release-process.md) — quy trình release

## Bảo mật

- [agnostic-security.md](agnostic-security.md) — mô hình bảo mật không phụ thuộc provider
- [frictionless-security.md](frictionless-security.md) — mặc định an toàn không gây cản trở
- [sandboxing.md](sandboxing.md) — Seatbelt / Landlock / Firejail / Bubblewrap
- [audit-logging.md](audit-logging.md) — audit log Merkle chain
- [matrix-e2ee-guide.md](matrix-e2ee-guide.md) — Matrix E2EE
- [security-roadmap.md](security-roadmap.md) — lộ trình bảo mật

## Phần cứng & ngoại vi

- [hardware-peripherals-design.md](hardware-peripherals-design.md) — kiến trúc ngoại vi
- [nucleo-setup.md](nucleo-setup.md) — STM32 Nucleo
- [arduino-uno-q-setup.md](arduino-uno-q-setup.md) — Arduino Uno Q
- [datasheets/nucleo-f401re.md](datasheets/nucleo-f401re.md), [datasheets/arduino-uno.md](datasheets/arduino-uno.md), [datasheets/esp32.md](datasheets/esp32.md)

## Đóng góp

- [../../../CONTRIBUTING.md](../../../CONTRIBUTING.md) *(tiếng Anh)*
- [pr-workflow.md](pr-workflow.md) — quản trị PR
- [reviewer-playbook.md](reviewer-playbook.md) — hướng dẫn reviewer
- [ci-map.md](ci-map.md) — bản đồ workflow CI
- [actions-source-policy.md](actions-source-policy.md) — chính sách nguồn GitHub Actions
- [../../contributing/cla.md](../../contributing/cla.md) — Contributor License Agreement *(tiếng Anh)*

## Kiến trúc & tham chiếu

- [../../architecture/adr-004-tool-shared-state-ownership.md](../../architecture/adr-004-tool-shared-state-ownership.md) *(tiếng Anh)*
- [../../architecture/adr-005-operator-liveness-and-rust-migration.md](../../architecture/adr-005-operator-liveness-and-rust-migration.md) *(tiếng Anh)*
- [reference/README.md](reference/README.md) — chỉ mục tham chiếu (vi)
- [../../reference/sop/connectivity.md](../../reference/sop/connectivity.md) *(tiếng Anh)*
- [../../reference/sop/observability.md](../../reference/sop/observability.md) *(tiếng Anh)*

## Giấy phép & nguồn upstream

- [`../../../NOTICE`](../../../NOTICE) — NOTICE gốc với attribution ZeroClaw được giữ nguyên theo Apache 2.0 §4(c)
- [`../../../LICENSE-MIT`](../../../LICENSE-MIT), [`../../../LICENSE-APACHE`](../../../LICENSE-APACHE) — văn bản giấy phép kép
- [docs/upstream/zeroclaw-attribution.md](../../upstream/zeroclaw-attribution.md) — Revka kế thừa gì từ ZeroClaw và checklist tuân thủ fork *(tiếng Anh)*
- [docs/maintainers/trademark.md](../../maintainers/trademark.md) — quy ước tên Revka và xác nhận thương hiệu ZeroClaw *(tiếng Anh)*

## Ngôn ngữ khác

- [English](../../README.md)
- [한국어](../ko/README.md)
- [简体中文](../zh-CN/README.md)
