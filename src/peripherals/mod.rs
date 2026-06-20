//! Hardware peripherals — STM32, RPi GPIO, etc.
//!
//! Peripherals extend the agent with physical capabilities. See
//! `docs/hardware-peripherals-design.md` for the full design.

pub mod traits;

#[cfg(feature = "hardware")]
pub mod serial;

#[cfg(feature = "hardware")]
pub mod arduino_flash;
#[cfg(feature = "hardware")]
pub mod arduino_upload;
#[cfg(feature = "hardware")]
pub mod capabilities_tool;
#[cfg(feature = "hardware")]
pub mod nucleo_flash;
#[cfg(feature = "hardware")]
pub mod uno_q_bridge;
#[cfg(feature = "hardware")]
pub mod uno_q_setup;

#[cfg(all(feature = "peripheral-rpi", target_os = "linux"))]
pub mod rpi;

#[cfg(any(feature = "hardware", feature = "peripheral-rpi"))]
pub use traits::Peripheral;

use crate::config::{Config, PeripheralBoardConfig, PeripheralsConfig};
#[cfg(feature = "hardware")]
use crate::tools::HardwareMemoryMapTool;
use crate::tools::Tool;
use anyhow::Result;

/// List configured boards from config (no connection yet).
pub fn list_configured_boards(config: &PeripheralsConfig) -> Vec<&PeripheralBoardConfig> {
    if !config.enabled {
        return Vec::new();
    }
    config.boards.iter().collect()
}

/// Handle `revka peripheral` subcommands.
#[allow(clippy::module_name_repetitions)]
pub async fn handle_command(cmd: crate::PeripheralCommands, config: &Config) -> Result<()> {
    match cmd {
        crate::PeripheralCommands::List => {
            let boards = list_configured_boards(&config.peripherals);
            if boards.is_empty() {
                println!("No peripherals configured.");
                println!();
                println!("Add one with: revka peripheral add <board> <path>");
                println!("  Example: revka peripheral add nucleo-f401re /dev/ttyACM0");
                println!();
                println!("Or add to config.toml:");
                println!("  [peripherals]");
                println!("  enabled = true");
                println!();
                println!("  [[peripherals.boards]]");
                println!("  board = \"nucleo-f401re\"");
                println!("  transport = \"serial\"");
                println!("  path = \"/dev/ttyACM0\"");
            } else {
                println!("Configured peripherals:");
                for b in boards {
                    let path = b.path.as_deref().unwrap_or("(native)");
                    println!("  {}  {}  {}", b.board, b.transport, path);
                }
            }
        }
        crate::PeripheralCommands::Add { board, path } => {
            let transport = if path == "native" { "native" } else { "serial" };
            let path_opt = if path == "native" {
                None
            } else {
                Some(path.clone())
            };

            let mut cfg = Box::pin(crate::config::Config::load_or_init()).await?;
            cfg.peripherals.enabled = true;

            if cfg
                .peripherals
                .boards
                .iter()
                .any(|b| b.board == board && b.path.as_deref() == path_opt.as_deref())
            {
                println!("Board {} at {:?} already configured.", board, path_opt);
                return Ok(());
            }

            cfg.peripherals.boards.push(PeripheralBoardConfig {
                board: board.clone(),
                transport: transport.to_string(),
                path: path_opt,
                baud: 115_200,
            });
            cfg.save().await?;
            println!("Added {} at {}. Restart daemon to apply.", board, path);
        }
        #[cfg(feature = "hardware")]
        crate::PeripheralCommands::Flash { port } => {
            let port_str = arduino_flash::resolve_port(config, port.as_deref())
                .or_else(|| port.clone())
                .ok_or_else(|| anyhow::anyhow!(
                    "No port specified. Use --port /dev/cu.usbmodem* or add arduino-uno to config.toml"
                ))?;
            arduino_flash::flash_arduino_firmware(&port_str)?;
        }
        #[cfg(not(feature = "hardware"))]
        crate::PeripheralCommands::Flash { .. } => {
            println!("Arduino flash requires the 'hardware' feature.");
            println!("Build with: cargo build --features hardware");
        }
        #[cfg(feature = "hardware")]
        crate::PeripheralCommands::SetupUnoQ { host } => {
            uno_q_setup::setup_uno_q_bridge(host.as_deref())?;
        }
        #[cfg(not(feature = "hardware"))]
        crate::PeripheralCommands::SetupUnoQ { .. } => {
            println!("Uno Q setup requires the 'hardware' feature.");
            println!("Build with: cargo build --features hardware");
        }
        #[cfg(feature = "hardware")]
        crate::PeripheralCommands::FlashNucleo => {
            nucleo_flash::flash_nucleo_firmware()?;
        }
        #[cfg(not(feature = "hardware"))]
        crate::PeripheralCommands::FlashNucleo => {
            println!("Nucleo flash requires the 'hardware' feature.");
            println!("Build with: cargo build --features hardware");
        }
    }
    Ok(())
}

/// Merge CLI `--peripheral board:path` overrides into the peripherals config.
///
/// Config-defined boards take precedence: an override naming a board already in
/// `config.boards` is ignored (and logged). `board:native` attaches via the
/// native transport; any other value after the first `:` is treated as the
/// serial path (baud defaults to 115200). A malformed override — missing `:`,
/// or an empty board/path — is a hard error rather than a silent no-op. Sets
/// `enabled` whenever an override is actually added.
///
/// Note: on non-`hardware` builds `create_peripheral_tools` is a no-op, so
/// merged boards only materialize as tools when built with `--features
/// hardware`; the merge itself is always performed so the flag is never
/// silently discarded.
pub fn apply_peripheral_overrides(
    config: &mut PeripheralsConfig,
    overrides: &[String],
) -> Result<()> {
    for spec in overrides {
        let (board, path) = spec.split_once(':').ok_or_else(|| {
            anyhow::anyhow!(
                "invalid --peripheral '{spec}': expected 'board:path' \
                 (e.g. nucleo-f401re:/dev/ttyACM0 or nucleo-f401re:native)"
            )
        })?;
        let (board, path) = (board.trim(), path.trim());
        anyhow::ensure!(
            !board.is_empty() && !path.is_empty(),
            "invalid --peripheral '{spec}': both board and path are required (board:path)"
        );
        if config.boards.iter().any(|b| b.board == board) {
            tracing::info!(
                board,
                "Peripheral override '--peripheral {spec}' ignored — config board takes precedence"
            );
            continue;
        }
        let (transport, path_opt) = if path == "native" {
            ("native".to_string(), None)
        } else {
            ("serial".to_string(), Some(path.to_string()))
        };
        config.boards.push(PeripheralBoardConfig {
            board: board.to_string(),
            transport,
            path: path_opt,
            baud: 115_200,
        });
        config.enabled = true;
        tracing::info!(board, "Attached peripheral from --peripheral override");
    }
    Ok(())
}

/// Create and connect peripherals from config, returning their tools.
/// Returns empty vec if peripherals disabled or hardware feature off.
#[cfg(feature = "hardware")]
pub async fn create_peripheral_tools(config: &PeripheralsConfig) -> Result<Vec<Box<dyn Tool>>> {
    if !config.enabled || config.boards.is_empty() {
        return Ok(Vec::new());
    }

    let mut tools: Vec<Box<dyn Tool>> = Vec::new();
    let mut serial_transports: Vec<(String, std::sync::Arc<serial::SerialTransport>)> = Vec::new();

    for board in &config.boards {
        // Arduino Uno Q: Bridge transport (socket to local Bridge app)
        if board.transport == "bridge" && (board.board == "arduino-uno-q" || board.board == "uno-q")
        {
            tools.push(Box::new(uno_q_bridge::UnoQGpioReadTool));
            tools.push(Box::new(uno_q_bridge::UnoQGpioWriteTool));
            tracing::info!(board = %board.board, "Uno Q Bridge GPIO tools added");
            continue;
        }

        // Native transport: RPi GPIO (Linux only)
        #[cfg(all(feature = "peripheral-rpi", target_os = "linux"))]
        if board.transport == "native"
            && (board.board == "rpi-gpio" || board.board == "raspberry-pi")
        {
            match rpi::RpiGpioPeripheral::connect_from_config(board).await {
                Ok(peripheral) => {
                    tools.extend(peripheral.tools());
                    tracing::info!(board = %board.board, "RPi GPIO peripheral connected");
                }
                Err(e) => {
                    tracing::warn!("Failed to connect RPi GPIO {}: {}", board.board, e);
                }
            }
            continue;
        }

        // Serial transport (STM32, ESP32, Arduino, etc.)
        if board.transport != "serial" {
            continue;
        }
        if board.path.is_none() {
            tracing::warn!("Skipping serial board {}: no path", board.board);
            continue;
        }

        match serial::SerialPeripheral::connect(board).await {
            Ok(peripheral) => {
                let mut p = peripheral;
                if p.connect().await.is_err() {
                    tracing::warn!("Peripheral {} connect warning (continuing)", p.name());
                }
                serial_transports.push((board.board.clone(), p.transport()));
                tools.extend(p.tools());
                if board.board == "arduino-uno" {
                    if let Some(ref path) = board.path {
                        tools.push(Box::new(arduino_upload::ArduinoUploadTool::new(
                            path.clone(),
                        )));
                        tracing::info!("Arduino upload tool added (port: {})", path);
                    }
                }
                tracing::info!(board = %board.board, "Serial peripheral connected");
            }
            Err(e) => {
                tracing::warn!("Failed to connect {}: {}", board.board, e);
            }
        }
    }

    // Phase B: Add hardware tools when any boards configured
    if !tools.is_empty() {
        let board_names: Vec<String> = config.boards.iter().map(|b| b.board.clone()).collect();
        tools.push(Box::new(HardwareMemoryMapTool::new(board_names.clone())));
        tools.push(Box::new(crate::tools::HardwareBoardInfoTool::new(
            board_names.clone(),
        )));
        tools.push(Box::new(crate::tools::HardwareMemoryReadTool::new(
            board_names,
        )));
    }

    // Phase C: Add hardware_capabilities tool when any serial boards
    if !serial_transports.is_empty() {
        tools.push(Box::new(capabilities_tool::HardwareCapabilitiesTool::new(
            serial_transports,
        )));
    }

    Ok(tools)
}

#[cfg(not(feature = "hardware"))]
#[allow(clippy::unused_async)]
pub async fn create_peripheral_tools(_config: &PeripheralsConfig) -> Result<Vec<Box<dyn Tool>>> {
    Ok(Vec::new())
}

/// Create probe-rs / static board info tools (hardware_board_info, hardware_memory_map,
/// hardware_memory_read). These use USB/probe-rs or static datasheet data — they never
/// open a serial port, so they are safe to register regardless of the `hardware` feature.
#[cfg(feature = "hardware")]
pub fn create_board_info_tools(config: &PeripheralsConfig) -> Vec<Box<dyn Tool>> {
    if !config.enabled || config.boards.is_empty() {
        return Vec::new();
    }
    let board_names: Vec<String> = config.boards.iter().map(|b| b.board.clone()).collect();
    vec![
        Box::new(crate::tools::HardwareMemoryMapTool::new(
            board_names.clone(),
        )),
        Box::new(crate::tools::HardwareBoardInfoTool::new(
            board_names.clone(),
        )),
        Box::new(crate::tools::HardwareMemoryReadTool::new(board_names)),
    ]
}

#[cfg(not(feature = "hardware"))]
pub fn create_board_info_tools(_config: &PeripheralsConfig) -> Vec<Box<dyn Tool>> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{PeripheralBoardConfig, PeripheralsConfig};

    #[test]
    fn list_configured_boards_when_disabled_returns_empty() {
        let config = PeripheralsConfig {
            enabled: false,
            boards: vec![PeripheralBoardConfig {
                board: "nucleo-f401re".into(),
                transport: "serial".into(),
                path: Some("/dev/ttyACM0".into()),
                baud: 115_200,
            }],
            datasheet_dir: None,
        };
        let result = list_configured_boards(&config);
        assert!(
            result.is_empty(),
            "disabled peripherals should return no boards"
        );
    }

    #[test]
    fn apply_overrides_adds_serial_board_and_enables() {
        let mut cfg = PeripheralsConfig::default();
        apply_peripheral_overrides(&mut cfg, &["nucleo-f401re:/dev/ttyACM0".to_string()]).unwrap();
        assert!(cfg.enabled);
        assert_eq!(cfg.boards.len(), 1);
        let b = &cfg.boards[0];
        assert_eq!(b.board, "nucleo-f401re");
        assert_eq!(b.transport, "serial");
        assert_eq!(b.path.as_deref(), Some("/dev/ttyACM0"));
        assert_eq!(b.baud, 115_200);
    }

    #[test]
    fn apply_overrides_native_transport_has_no_path() {
        let mut cfg = PeripheralsConfig::default();
        apply_peripheral_overrides(&mut cfg, &["rpi-gpio:native".to_string()]).unwrap();
        assert_eq!(cfg.boards[0].transport, "native");
        assert!(cfg.boards[0].path.is_none());
    }

    #[test]
    fn apply_overrides_config_board_takes_precedence() {
        let mut cfg = PeripheralsConfig {
            enabled: true,
            boards: vec![PeripheralBoardConfig {
                board: "nucleo-f401re".into(),
                transport: "serial".into(),
                path: Some("/dev/ttyUSB0".into()),
                baud: 115_200,
            }],
            datasheet_dir: None,
        };
        apply_peripheral_overrides(&mut cfg, &["nucleo-f401re:/dev/ttyACM0".to_string()]).unwrap();
        // Override ignored — config board wins; original path retained.
        assert_eq!(cfg.boards.len(), 1);
        assert_eq!(cfg.boards[0].path.as_deref(), Some("/dev/ttyUSB0"));
    }

    #[test]
    fn apply_overrides_rejects_malformed() {
        let mut cfg = PeripheralsConfig::default();
        assert!(apply_peripheral_overrides(&mut cfg, &["no-colon".to_string()]).is_err());
        assert!(apply_peripheral_overrides(&mut cfg, &[":/dev/ttyACM0".to_string()]).is_err());
        assert!(apply_peripheral_overrides(&mut cfg, &["board:".to_string()]).is_err());
    }

    #[test]
    fn list_configured_boards_when_enabled_with_boards() {
        let config = PeripheralsConfig {
            enabled: true,
            boards: vec![
                PeripheralBoardConfig {
                    board: "nucleo-f401re".into(),
                    transport: "serial".into(),
                    path: Some("/dev/ttyACM0".into()),
                    baud: 115_200,
                },
                PeripheralBoardConfig {
                    board: "rpi-gpio".into(),
                    transport: "native".into(),
                    path: None,
                    baud: 115_200,
                },
            ],
            datasheet_dir: None,
        };
        let result = list_configured_boards(&config);
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].board, "nucleo-f401re");
        assert_eq!(result[1].board, "rpi-gpio");
    }

    #[test]
    fn list_configured_boards_when_enabled_but_no_boards() {
        let config = PeripheralsConfig {
            enabled: true,
            boards: vec![],
            datasheet_dir: None,
        };
        let result = list_configured_boards(&config);
        assert!(
            result.is_empty(),
            "enabled with no boards should return empty"
        );
    }

    #[tokio::test]
    async fn create_peripheral_tools_returns_empty_when_disabled() {
        let config = PeripheralsConfig {
            enabled: false,
            boards: vec![],
            datasheet_dir: None,
        };
        let tools = create_peripheral_tools(&config).await.unwrap();
        assert!(
            tools.is_empty(),
            "disabled peripherals should produce no tools"
        );
    }
}
