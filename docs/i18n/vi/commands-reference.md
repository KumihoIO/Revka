# Tham khảo lệnh Construct

_Source English version updated 2026-04-21; localized version may be stale until retranslated._

Dựa trên CLI hiện tại (`construct --help`).

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

- `construct onboard`
- `construct onboard --channels-only`
- `construct onboard --api-key <KEY> --provider <ID> --memory <sqlite|lucid|markdown|none>`
- `construct onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <sqlite|lucid|markdown|none>`

### `agent`

- `construct agent`
- `construct agent -m "Hello"`
- `construct agent --provider <ID> --model <MODEL> --temperature <0.0-2.0>`
- `construct agent --peripheral <board:path>`

### `gateway` / `daemon`

- `construct gateway [--host <HOST>] [--port <PORT>]`
- `construct daemon [--host <HOST>] [--port <PORT>]`

Ghi chú:

- `gateway` phục vụ dashboard React nhúng, REST API, SSE (`/api/events`) và
  các WebSocket endpoint (`/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`).
- `/ws/chat` nhận `{"type":"message","content":"..."}` để bắt đầu một lượt,
  `{"type":"steer","content":"..."}` khi lượt đang chạy để điều chỉnh bước
  tiếp theo, và `{"type":"stop"}` để hủy lượt đang chạy.

### `service`

- `construct service install`
- `construct service start`
- `construct service stop`
- `construct service restart`
- `construct service status`
- `construct service uninstall`

### `cron`

- `construct cron list`
- `construct cron add <expr> [--tz <IANA_TZ>] <command>`
- `construct cron add-at <rfc3339_timestamp> <command>`
- `construct cron add-every <every_ms> <command>`
- `construct cron once <delay> <command>`
- `construct cron remove <id>`
- `construct cron pause <id>`
- `construct cron resume <id>`

### `models`

- `construct models refresh`
- `construct models refresh --provider <ID>`
- `construct models refresh --force`

`models refresh` hiện hỗ trợ làm mới danh mục trực tiếp cho các provider: `openrouter`, `openai`, `anthropic`, `groq`, `mistral`, `deepseek`, `xai`, `together-ai`, `gemini`, `ollama`, `astrai`, `venice`, `fireworks`, `cohere`, `moonshot`, `glm`, `zai`, `qwen` và `nvidia`.

### `channel`

- `construct channel list`
- `construct channel start`
- `construct channel doctor`
- `construct channel bind-telegram <IDENTITY>`
- `construct channel add <type> <json>`
- `construct channel remove <name>`

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

- `construct integrations info <name>`

### `skills`

- `construct skills list`
- `construct skills install <source>`
- `construct skills remove <name>`

`<source>` chấp nhận git remote (`https://...`, `http://...`, `ssh://...` và `git@host:owner/repo.git`) hoặc đường dẫn cục bộ.

Skill manifest (`SKILL.toml`) hỗ trợ `prompts` và `[[tools]]`; cả hai được đưa vào system prompt của agent khi chạy, giúp model có thể tuân theo hướng dẫn skill mà không cần đọc thủ công.

### `migrate`

- `construct migrate openclaw [--source <path>] [--dry-run]`

### `config`

- `construct config schema`

`config schema` xuất JSON Schema (draft 2020-12) cho toàn bộ hợp đồng `config.toml` ra stdout.

### `completions`

- `construct completions bash`
- `construct completions fish`
- `construct completions zsh`
- `construct completions powershell`
- `construct completions elvish`

`completions` chỉ xuất ra stdout để script có thể được source trực tiếp mà không bị lẫn log/cảnh báo.

### `hardware`

- `construct hardware discover`
- `construct hardware introspect <path>`
- `construct hardware info [--chip <chip_name>]`

### `peripheral`

- `construct peripheral list`
- `construct peripheral add <board> <path>`
- `construct peripheral flash [--port <serial_port>]`
- `construct peripheral setup-uno-q [--host <ip_or_host>]`
- `construct peripheral flash-nucleo`

## Kiểm tra nhanh

Để xác minh nhanh tài liệu với binary hiện tại:

```bash
construct --help
construct <command> --help
```
