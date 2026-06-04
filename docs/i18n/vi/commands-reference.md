# Tham khảo lệnh Revka

_Source English version updated 2026-04-21; localized version may be stale until retranslated._

Dựa trên CLI hiện tại (`revka --help`).

Xác minh lần cuối: **2026-02-20**.

## Lệnh cấp cao nhất

| Lệnh | Mục đích |
|---|---|
| `onboard` | Khởi tạo workspace/config nhanh hoặc tương tác |
| `agent` | Chạy chat tương tác hoặc chế độ gửi tin nhắn đơn |
| `gateway` | Khởi động gateway webhook và HTTP WhatsApp |
| `daemon` | Khởi động runtime có giám sát (gateway + channels + heartbeat/scheduler tùy chọn) |
| `service` | Quản lý vòng đời dịch vụ cấp hệ điều hành |
| `doctor` | Chạy chẩn đoán và kiểm tra trạng thái |
| `status` | Hiển thị cấu hình và tóm tắt hệ thống |
| `cron` | Quản lý tác vụ định kỳ |
| `models` | Làm mới danh mục model của provider |
| `providers` | Liệt kê ID provider, bí danh và provider đang dùng |
| `channel` | Quản lý kênh và kiểm tra sức khỏe kênh |
| `integrations` | Kiểm tra chi tiết tích hợp |
| `skills` | Liệt kê/cài đặt/gỡ bỏ skills |
| `migrate` | Nhập dữ liệu từ runtime khác (hiện hỗ trợ OpenClaw) |
| `config` | Xuất schema cấu hình dạng máy đọc được |
| `completions` | Tạo script tự hoàn thành cho shell ra stdout |
| `hardware` | Phát hiện và kiểm tra phần cứng USB |
| `peripheral` | Cấu hình và nạp firmware thiết bị ngoại vi |

## Nhóm lệnh

### `onboard`

- `revka onboard`
- `revka onboard --channels-only`
- `revka onboard --api-key <KEY> --provider <ID> --memory <sqlite|lucid|markdown|none>`
- `revka onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <sqlite|lucid|markdown|none>`

### `agent`

- `revka agent`
- `revka agent -m "Hello"`
- `revka agent --provider <ID> --model <MODEL> --temperature <0.0-2.0>`
- `revka agent --peripheral <board:path>`

### `gateway` / `daemon`

- `revka gateway [--host <HOST>] [--port <PORT>]`
- `revka daemon [--host <HOST>] [--port <PORT>]`

Ghi chú:

- `gateway` phục vụ dashboard React nhúng, REST API, SSE (`/api/events`) và
  các WebSocket endpoint (`/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`).
- `/ws/chat` nhận `{"type":"message","content":"..."}` để bắt đầu một lượt,
  `{"type":"steer","content":"..."}` khi lượt đang chạy để điều chỉnh bước
  tiếp theo, và `{"type":"stop"}` để hủy lượt đang chạy.

### `service`

- `revka service install`
- `revka service start`
- `revka service stop`
- `revka service restart`
- `revka service status`
- `revka service uninstall`

### `cron`

- `revka cron list`
- `revka cron add <expr> [--tz <IANA_TZ>] <command>`
- `revka cron add-at <rfc3339_timestamp> <command>`
- `revka cron add-every <every_ms> <command>`
- `revka cron once <delay> <command>`
- `revka cron remove <id>`
- `revka cron pause <id>`
- `revka cron resume <id>`

### `models`

- `revka models refresh`
- `revka models refresh --provider <ID>`
- `revka models refresh --force`

`models refresh` hiện hỗ trợ làm mới danh mục trực tiếp cho các provider: `openrouter`, `openai`, `anthropic`, `groq`, `mistral`, `deepseek`, `xai`, `together-ai`, `gemini`, `ollama`, `astrai`, `venice`, `fireworks`, `cohere`, `moonshot`, `glm`, `zai`, `qwen` và `nvidia`.

### `channel`

- `revka channel list`
- `revka channel start`
- `revka channel doctor`
- `revka channel bind-telegram <IDENTITY>`
- `revka channel add <type> <json>`
- `revka channel remove <name>`

Lệnh trong chat khi runtime đang chạy (Telegram/Discord):

- `/models`
- `/models <provider>`
- `/model`
- `/model <model-id>`

Channel runtime cũng theo dõi `config.toml` và tự động áp dụng thay đổi cho:
- `default_provider`
- `default_model`
- `default_temperature`
- `api_key` / `api_url` (cho provider mặc định)
- `reliability.*` cài đặt retry của provider

`add/remove` hiện chuyển hướng về thiết lập có hướng dẫn / cấu hình thủ công (chưa hỗ trợ đầy đủ mutator khai báo).

### `integrations`

- `revka integrations info <name>`

### `skills`

- `revka skills list`
- `revka skills install <source>`
- `revka skills remove <name>`

`<source>` chấp nhận git remote (`https://...`, `http://...`, `ssh://...` và `git@host:owner/repo.git`) hoặc đường dẫn cục bộ.

Skill manifest (`SKILL.toml`) hỗ trợ `prompts` và `[[tools]]`; cả hai được đưa vào system prompt của agent khi chạy, giúp model có thể tuân theo hướng dẫn skill mà không cần đọc thủ công.

### `migrate`

- `revka migrate openclaw [--source <path>] [--dry-run]`

### `config`

- `revka config schema`

`config schema` xuất JSON Schema (draft 2020-12) cho toàn bộ hợp đồng `config.toml` ra stdout.

### `completions`

- `revka completions bash`
- `revka completions fish`
- `revka completions zsh`
- `revka completions powershell`
- `revka completions elvish`

`completions` chỉ xuất ra stdout để script có thể được source trực tiếp mà không bị lẫn log/cảnh báo.

### `hardware`

- `revka hardware discover`
- `revka hardware introspect <path>`
- `revka hardware info [--chip <chip_name>]`

### `peripheral`

- `revka peripheral list`
- `revka peripheral add <board> <path>`
- `revka peripheral flash [--port <serial_port>]`
- `revka peripheral setup-uno-q [--host <ip_or_host>]`
- `revka peripheral flash-nucleo`

## Kiểm tra nhanh

Để xác minh nhanh tài liệu với binary hiện tại:

```bash
revka --help
revka <command> --help
```
