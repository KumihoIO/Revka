//! Safety System - Collision avoidance, watchdogs, and emergency stops
//!
//! This module runs INDEPENDENTLY of the AI brain to ensure safety
//! even if the LLM makes bad decisions or hangs.
//!
//! ## Safety Layers
//!
//! 1. **Pre-move checks** - Verify path clear before any movement
//! 2. **Active monitoring** - Continuous sensor polling during movement
//! 3. **Reactive stops** - Instant halt on obstacle detection
//! 4. **Watchdog timer** - Latched emergency-stop if no commands for N seconds
//! 5. **Hardware E-stop** - Physical button overrides everything
//!
//! ## Design Philosophy
//!
//! The AI can REQUEST movement, but the safety system ALLOWS it.
//! Safety always wins.

use crate::config::{RobotConfig, SafetyConfig};
use crate::traits::ToolResult;
use anyhow::Result;
use portable_atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};
use tokio::sync::{RwLock, broadcast};

/// Safety events broadcast to all listeners
#[derive(Debug, Clone)]
pub enum SafetyEvent {
    /// Obstacle detected, movement blocked
    ObstacleDetected { distance: f64, angle: u16 },
    /// Emergency stop triggered
    EmergencyStop { reason: String },
    /// Watchdog timeout - no activity
    WatchdogTimeout,
    /// Movement approved
    MovementApproved,
    /// Movement denied with reason
    MovementDenied { reason: String },
    /// Bump sensor triggered
    BumpDetected { sensor: String },
    /// System recovered, ready to move again
    Recovered,
}

/// Real-time safety state
pub struct SafetyState {
    /// Is it safe to move?
    pub can_move: AtomicBool,
    /// Emergency stop active?
    pub estop_active: AtomicBool,
    /// Last movement command timestamp (ms since epoch)
    pub last_command_ms: AtomicU64,
    /// Watchdog (dead-man's switch) has tripped: no commands within the timeout.
    /// Distinct from `estop_active` so it auto-recovers on the next command
    /// rather than requiring an explicit `reset_estop` (#439).
    pub watchdog_tripped: AtomicBool,
    /// Current minimum distance to obstacle
    pub min_obstacle_distance: RwLock<f64>,
    /// Reason movement is blocked (if any)
    pub block_reason: RwLock<Option<String>>,
    /// Speed multiplier based on proximity (0.0 - 1.0)
    pub speed_limit: RwLock<f64>,
}

impl Default for SafetyState {
    fn default() -> Self {
        Self {
            can_move: AtomicBool::new(true),
            estop_active: AtomicBool::new(false),
            last_command_ms: AtomicU64::new(0),
            watchdog_tripped: AtomicBool::new(false),
            min_obstacle_distance: RwLock::new(999.0),
            block_reason: RwLock::new(None),
            speed_limit: RwLock::new(1.0),
        }
    }
}

/// Safety monitor - runs as background task
pub struct SafetyMonitor {
    config: SafetyConfig,
    state: Arc<SafetyState>,
    event_tx: broadcast::Sender<SafetyEvent>,
    shutdown: AtomicBool,
}

impl SafetyMonitor {
    pub fn new(config: SafetyConfig) -> (Self, broadcast::Receiver<SafetyEvent>) {
        let (event_tx, event_rx) = broadcast::channel(64);
        let monitor = Self {
            config,
            state: Arc::new(SafetyState::default()),
            event_tx,
            shutdown: AtomicBool::new(false),
        };
        (monitor, event_rx)
    }

    pub fn state(&self) -> Arc<SafetyState> {
        self.state.clone()
    }

    pub fn subscribe(&self) -> broadcast::Receiver<SafetyEvent> {
        self.event_tx.subscribe()
    }

    /// Check if movement is currently allowed
    pub async fn can_move(&self) -> bool {
        // estop and the watchdog dead-man's switch are independent gates over the
        // obstacle/bump/stale `can_move` bool, so neither can be silently undone
        // by a `can_move = true` writer (e.g. a clear sensor reading) (#439).
        if self.state.estop_active.load(Ordering::SeqCst)
            || self.state.watchdog_tripped.load(Ordering::SeqCst)
        {
            return false;
        }
        self.state.can_move.load(Ordering::SeqCst)
    }

    /// Get current speed limit multiplier (0.0 - 1.0)
    pub async fn speed_limit(&self) -> f64 {
        *self.state.speed_limit.read().await
    }

    /// Request permission to move - returns allowed speed multiplier or error
    pub async fn request_movement(&self, direction: &str, distance: f64) -> Result<f64, String> {
        // Check E-stop
        if self.state.estop_active.load(Ordering::SeqCst) {
            return Err("Emergency stop active".to_string());
        }

        // A fresh command means the controller is alive again, so clear the
        // watchdog gate (#439). This only lifts the dead-man's switch — it does
        // NOT touch `can_move`, so any concurrent obstacle / bump / sensor-stale
        // block still rejects the move via the check below.
        if self.state.watchdog_tripped.swap(false, Ordering::SeqCst) {
            let _ = self.event_tx.send(SafetyEvent::Recovered);
        }

        // Check general movement permission
        if !self.state.can_move.load(Ordering::SeqCst) {
            let reason = self.state.block_reason.read().await;
            return Err(reason
                .clone()
                .unwrap_or_else(|| "Movement blocked".to_string()));
        }

        // Check obstacle distance in movement direction
        let min_dist = *self.state.min_obstacle_distance.read().await;
        if min_dist < self.config.min_obstacle_distance {
            let msg = format!(
                "Obstacle too close: {:.2}m (min: {:.2}m)",
                min_dist, self.config.min_obstacle_distance
            );
            let _ = self.event_tx.send(SafetyEvent::MovementDenied {
                reason: msg.clone(),
            });
            return Err(msg);
        }

        // Check if requested distance would hit obstacle
        if distance > min_dist - self.config.min_obstacle_distance {
            let safe_distance = (min_dist - self.config.min_obstacle_distance).max(0.0);
            if safe_distance < 0.1 {
                return Err(format!(
                    "Cannot move {}: obstacle at {:.2}m",
                    direction, min_dist
                ));
            }
            // Allow reduced distance
            tracing::warn!(
                "Reducing {} distance from {:.2}m to {:.2}m due to obstacle",
                direction,
                distance,
                safe_distance
            );
        }

        // Update last command time
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        self.state.last_command_ms.store(now_ms, Ordering::SeqCst);

        // Calculate speed limit based on proximity
        let speed_mult = self.calculate_speed_limit(min_dist).await;

        let _ = self.event_tx.send(SafetyEvent::MovementApproved);
        Ok(speed_mult)
    }

    /// Calculate safe speed based on obstacle proximity
    async fn calculate_speed_limit(&self, obstacle_distance: f64) -> f64 {
        let min_dist = self.config.min_obstacle_distance;
        let slow_zone = min_dist * 3.0; // Start slowing at 3x minimum distance

        let limit = if obstacle_distance >= slow_zone {
            1.0 // Full speed
        } else if obstacle_distance <= min_dist {
            0.0 // Stop
        } else {
            // Linear interpolation between stop and full speed
            (obstacle_distance - min_dist) / (slow_zone - min_dist)
        };

        *self.state.speed_limit.write().await = limit;
        limit
    }

    /// Trigger emergency stop
    pub async fn emergency_stop(&self, reason: &str) {
        tracing::error!("EMERGENCY STOP: {}", reason);
        self.state.estop_active.store(true, Ordering::SeqCst);
        self.state.can_move.store(false, Ordering::SeqCst);
        *self.state.block_reason.write().await = Some(reason.to_string());

        let _ = self.event_tx.send(SafetyEvent::EmergencyStop {
            reason: reason.to_string(),
        });
    }

    /// Reset emergency stop (requires explicit action)
    pub async fn reset_estop(&self) {
        tracing::info!("E-STOP RESET");
        self.state.estop_active.store(false, Ordering::SeqCst);
        self.state.can_move.store(true, Ordering::SeqCst);
        *self.state.block_reason.write().await = None;

        let _ = self.event_tx.send(SafetyEvent::Recovered);
    }

    /// Update obstacle distance (call from sensor loop)
    pub async fn update_obstacle_distance(&self, distance: f64, angle: u16) {
        // Update minimum distance tracking
        {
            let mut min_dist = self.state.min_obstacle_distance.write().await;
            // Always update to current reading (not just if closer)
            *min_dist = distance;
        }

        // Recalculate speed limit based on new distance
        self.calculate_speed_limit(distance).await;

        // Check if too close
        if distance < self.config.min_obstacle_distance {
            self.state.can_move.store(false, Ordering::SeqCst);
            *self.state.block_reason.write().await =
                Some(format!("Obstacle at {:.2}m ({}°)", distance, angle));

            let _ = self
                .event_tx
                .send(SafetyEvent::ObstacleDetected { distance, angle });
        } else if !self.state.estop_active.load(Ordering::SeqCst) {
            // Clear the obstacle block when the obstacle moves away. This only
            // manages the obstacle dimension of `can_move`; the watchdog is an
            // independent gate in `can_move()`, so a clear reading re-enabling
            // `can_move` here does NOT undo a dead-man's-switch trip (#439).
            self.state.can_move.store(true, Ordering::SeqCst);
            *self.state.block_reason.write().await = None;
        }
    }

    /// Report bump sensor triggered
    pub async fn bump_detected(&self, sensor: &str) {
        tracing::warn!("BUMP DETECTED: {}", sensor);

        // Immediate stop
        self.state.can_move.store(false, Ordering::SeqCst);
        *self.state.block_reason.write().await = Some(format!("Bump: {}", sensor));

        let _ = self.event_tx.send(SafetyEvent::BumpDetected {
            sensor: sensor.to_string(),
        });

        // Auto-recover after brief pause (robot should back up)
        tokio::spawn({
            let state = self.state.clone();
            let event_tx = self.event_tx.clone();
            async move {
                tokio::time::sleep(Duration::from_secs(2)).await;
                if !state.estop_active.load(Ordering::SeqCst) {
                    state.can_move.store(true, Ordering::SeqCst);
                    *state.block_reason.write().await = None;
                    let _ = event_tx.send(SafetyEvent::Recovered);
                }
            }
        });
    }

    /// Shutdown the monitor
    pub fn shutdown(&self) {
        self.shutdown.store(true, Ordering::SeqCst);
    }

    /// Watchdog (dead-man's switch): block movement if no command has been
    /// approved within `watchdog_timeout` ("auto-stop if no commands for N
    /// seconds"). Returns `true` if it tripped this call (#439).
    ///
    /// It sets ONLY the dedicated `watchdog_tripped` flag, which `can_move()`
    /// consults directly — deliberately not the shared `can_move` bool nor an
    /// `emergency_stop`. This keeps the watchdog an independent gate that (a)
    /// cannot be silently cleared by the obstacle / bump / sensor-stale writers
    /// of `can_move`, and (b) does not itself clobber any of those blocks. It is
    /// not a hard e-stop (which has no in-tree `reset_estop` caller and would
    /// wedge the robot); instead it **auto-recovers on the next command** in
    /// `request_movement`.
    async fn check_watchdog(&self, watchdog_timeout: Duration) -> bool {
        let last_cmd_ms = self.state.last_command_ms.load(Ordering::SeqCst);
        // 0 = disarmed (no command yet / explicit stop). Fire at most once per
        // silent episode; the next approved command clears the trip and re-arms.
        if last_cmd_ms == 0 || self.state.watchdog_tripped.load(Ordering::SeqCst) {
            return false;
        }
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        // saturating_sub guards against wall-clock skew (NTP/manual set) that
        // would otherwise underflow and wrap to a huge elapsed time (#439).
        let elapsed = Duration::from_millis(now_ms.saturating_sub(last_cmd_ms));
        if elapsed > watchdog_timeout {
            tracing::warn!("Watchdog timeout - no commands for {elapsed:?}; blocking movement");
            self.state.watchdog_tripped.store(true, Ordering::SeqCst);
            let _ = self.event_tx.send(SafetyEvent::WatchdogTimeout);
            return true;
        }
        false
    }

    /// Disarm the watchdog on an **explicit stop** (and lift any active trip). A
    /// deliberate stop means the operator intends the robot to be idle, so the
    /// dead-man's switch should not trip during that intentional idle. It is
    /// intentionally NOT called on normal drive completion — the watchdog must
    /// keep counting through the idle-between-commands window, which is the case
    /// it exists to protect; see `check_watchdog`.
    pub fn record_drive_finished(&self) {
        self.state.last_command_ms.store(0, Ordering::SeqCst);
        self.state.watchdog_tripped.store(false, Ordering::SeqCst);
    }

    /// Run the safety monitor loop (call in background task)
    pub async fn run(&self, mut sensor_rx: tokio::sync::mpsc::Receiver<SensorReading>) {
        let watchdog_timeout = Duration::from_secs(self.config.max_drive_duration);
        let mut last_sensor_update = Instant::now();

        // A standalone interval — not an inline `sleep` inside the select! — so
        // the periodic safety checks (sensor-stale + watchdog) cannot be starved
        // by a steady stream of sensor readings. The interval's deadline persists
        // across iterations, whereas a fresh `sleep` future restarts from zero
        // every time the recv() branch wins, which (at typical LIDAR cadence)
        // means the timer branch would essentially never elapse (#439).
        let mut safety_tick = tokio::time::interval(Duration::from_secs(1));
        safety_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

        while !self.shutdown.load(Ordering::SeqCst) {
            tokio::select! {
                // Process sensor readings
                Some(reading) = sensor_rx.recv() => {
                    last_sensor_update = Instant::now();
                    match reading {
                        SensorReading::Lidar { distance, angle } => {
                            self.update_obstacle_distance(distance, angle).await;
                        }
                        SensorReading::Bump { sensor } => {
                            self.bump_detected(&sensor).await;
                        }
                        SensorReading::Estop { pressed } => {
                            if pressed {
                                self.emergency_stop("Hardware E-stop pressed").await;
                            }
                        }
                    }
                }

                // Periodic safety checks (stale-sensor + watchdog), ~1Hz.
                _ = safety_tick.tick() => {
                    // Check for sensor timeout
                    if last_sensor_update.elapsed() > Duration::from_secs(5) {
                        tracing::warn!("Sensor data stale - blocking movement");
                        self.state.can_move.store(false, Ordering::SeqCst);
                        *self.state.block_reason.write().await =
                            Some("Sensor data stale".to_string());
                    }

                    // Watchdog dead-man's switch: latched e-stop if commands stop.
                    self.check_watchdog(watchdog_timeout).await;
                }
            }
        }
    }
}

/// Sensor readings fed to safety monitor
#[derive(Debug, Clone)]
pub enum SensorReading {
    Lidar { distance: f64, angle: u16 },
    Bump { sensor: String },
    Estop { pressed: bool },
}

/// Safety-aware drive wrapper
/// Wraps the drive tool to enforce safety limits
pub struct SafeDrive {
    inner_drive: Arc<dyn crate::traits::Tool>,
    safety: Arc<SafetyMonitor>,
}

impl SafeDrive {
    pub fn new(drive: Arc<dyn crate::traits::Tool>, safety: Arc<SafetyMonitor>) -> Self {
        Self {
            inner_drive: drive,
            safety,
        }
    }
}

#[async_trait::async_trait]
impl crate::traits::Tool for SafeDrive {
    fn name(&self) -> &str {
        "drive"
    }

    fn description(&self) -> &str {
        "Move the robot (with safety limits enforced)"
    }

    fn parameters_schema(&self) -> serde_json::Value {
        self.inner_drive.parameters_schema()
    }

    async fn execute(&self, args: serde_json::Value) -> Result<ToolResult> {
        // ToolResult imported at top of file

        let action = args["action"].as_str().unwrap_or("unknown");
        let distance = args["distance"].as_f64().unwrap_or(0.5);

        // Always allow stop — and disarm the watchdog, since a stopped robot is
        // not driving and must not later latch an e-stop while idle (#439).
        if action == "stop" {
            self.safety.record_drive_finished();
            return self.inner_drive.execute(args).await;
        }

        // Request permission from safety system
        match self.safety.request_movement(action, distance).await {
            Ok(speed_mult) => {
                // Modify speed in args
                let mut modified_args = args.clone();
                let original_speed = args["speed"].as_f64().unwrap_or(0.5);
                modified_args["speed"] = serde_json::json!(original_speed * speed_mult);

                if speed_mult < 1.0 {
                    tracing::info!(
                        "Safety: Reducing speed to {:.0}% due to obstacle proximity",
                        speed_mult * 100.0
                    );
                }

                // Note: the watchdog is deliberately left armed after a drive
                // completes — the idle-between-commands window is exactly what
                // the dead-man's switch protects (#439). It auto-recovers on the
                // next command and is disarmed only by an explicit `stop`.
                self.inner_drive.execute(modified_args).await
            }
            Err(reason) => Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!("Safety blocked movement: {}", reason)),
            }),
        }
    }
}

/// Pre-flight safety check before any operation
pub async fn preflight_check(config: &RobotConfig) -> Result<Vec<String>> {
    let mut warnings = Vec::new();

    // Check safety config
    if config.safety.min_obstacle_distance < 0.1 {
        warnings.push("WARNING: min_obstacle_distance < 0.1m is dangerously low".to_string());
    }

    if config.safety.max_drive_duration > 60 {
        warnings.push("WARNING: max_drive_duration > 60s may allow runaway".to_string());
    }

    if config.drive.max_speed > 1.0 {
        warnings.push("WARNING: max_speed > 1.0 m/s is very fast for indoor use".to_string());
    }

    if config.safety.estop_pin.is_none() {
        warnings.push(
            "WARNING: No E-stop pin configured. Recommend wiring a hardware stop button."
                .to_string(),
        );
    }

    // Check for sensor availability
    if config.sensors.lidar_type == "mock" {
        warnings.push("NOTICE: LIDAR in mock mode - no real obstacle detection".to_string());
    }

    Ok(warnings)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn safety_state_defaults() {
        let state = SafetyState::default();
        assert!(state.can_move.load(Ordering::SeqCst));
        assert!(!state.estop_active.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn safety_monitor_blocks_on_obstacle() {
        let config = SafetyConfig::default();

        let (monitor, _rx) = SafetyMonitor::new(config);

        // Initially can move
        assert!(monitor.can_move().await);

        // Report close obstacle
        monitor.update_obstacle_distance(0.2, 0).await;

        // Now blocked
        assert!(!monitor.can_move().await);
    }

    #[tokio::test]
    async fn safety_monitor_estop() {
        let config = SafetyConfig::default();
        let (monitor, mut rx) = SafetyMonitor::new(config);

        monitor.emergency_stop("test").await;

        assert!(!monitor.can_move().await);
        assert!(monitor.state.estop_active.load(Ordering::SeqCst));

        // Check event was sent
        let event = rx.try_recv().unwrap();
        matches!(event, SafetyEvent::EmergencyStop { .. });
    }

    #[tokio::test]
    async fn speed_limit_calculation() {
        let config = SafetyConfig {
            min_obstacle_distance: 0.3,
            ..Default::default()
        };
        let (monitor, _rx) = SafetyMonitor::new(config);

        // Far obstacle = full speed
        let speed = monitor.calculate_speed_limit(2.0).await;
        assert!((speed - 1.0).abs() < 0.01);

        // Close obstacle = reduced speed
        let speed = monitor.calculate_speed_limit(0.5).await;
        assert!(speed < 1.0);
        assert!(speed > 0.0);

        // At minimum = stop
        let speed = monitor.calculate_speed_limit(0.3).await;
        assert!((speed - 0.0).abs() < 0.01);
    }

    #[tokio::test]
    async fn watchdog_timeout_blocks_movement_then_recovers_on_next_command() {
        // #439: command-silence must actually stop the robot ("auto-stop if no
        // commands for N seconds"), without a clear sensor reading silently
        // re-enabling it, and must auto-recover when a command returns (not a
        // hard, unrecoverable e-stop).
        let (monitor, _rx) = SafetyMonitor::new(SafetyConfig::default());
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        // Arm the watchdog with a command timestamp well past the 30s timeout.
        monitor
            .state
            .last_command_ms
            .store(now_ms - 60_000, Ordering::SeqCst);

        assert!(monitor.check_watchdog(Duration::from_secs(30)).await);
        assert!(monitor.state.watchdog_tripped.load(Ordering::SeqCst));
        assert!(
            !monitor.state.estop_active.load(Ordering::SeqCst),
            "watchdog must not latch a hard e-stop"
        );
        assert!(!monitor.can_move().await);

        // Fires once per silent episode — does not re-trip every tick.
        assert!(!monitor.check_watchdog(Duration::from_secs(30)).await);

        // A clear sensor reading must NOT silently re-enable movement.
        monitor.update_obstacle_distance(5.0, 0).await;
        assert!(
            !monitor.can_move().await,
            "watchdog block must survive clear sensor readings"
        );
        assert!(monitor.state.watchdog_tripped.load(Ordering::SeqCst));

        // A fresh command (controller is back) auto-recovers — no manual reset.
        assert!(monitor.request_movement("forward", 0.1).await.is_ok());
        assert!(!monitor.state.watchdog_tripped.load(Ordering::SeqCst));
        assert!(monitor.can_move().await);
    }

    #[tokio::test]
    async fn explicit_stop_disarms_watchdog() {
        // A deliberate stop should not later trip the dead-man's switch during the
        // intentional idle (SafeDrive calls record_drive_finished on `stop`).
        let (monitor, _rx) = SafetyMonitor::new(SafetyConfig::default());
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        monitor
            .state
            .last_command_ms
            .store(now_ms - 60_000, Ordering::SeqCst);

        monitor.record_drive_finished();
        assert_eq!(monitor.state.last_command_ms.load(Ordering::SeqCst), 0);
        assert!(!monitor.check_watchdog(Duration::from_secs(30)).await);
        assert!(monitor.can_move().await);
    }

    #[tokio::test]
    async fn watchdog_recovery_does_not_clobber_a_stale_block() {
        // #439 review: when the watchdog AND a sensor-stale block are both active,
        // a fresh command must not re-enable a blind robot — the stale block must
        // still reject the move (the watchdog clears, the stale block does not).
        let (monitor, _rx) = SafetyMonitor::new(SafetyConfig::default());
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        monitor
            .state
            .last_command_ms
            .store(now_ms - 60_000, Ordering::SeqCst);
        // Simulate a concurrent sensor-stale block.
        monitor.state.can_move.store(false, Ordering::SeqCst);
        *monitor.state.block_reason.write().await = Some("Sensor data stale".to_string());

        assert!(monitor.check_watchdog(Duration::from_secs(30)).await);

        let err = monitor
            .request_movement("forward", 0.1)
            .await
            .expect_err("stale block must still reject the move");
        assert!(err.contains("stale"), "got: {err}");
        // The watchdog cleared (controller is back) but the stale block remains.
        assert!(!monitor.state.watchdog_tripped.load(Ordering::SeqCst));
        assert!(!monitor.can_move().await);
    }

    #[tokio::test]
    async fn watchdog_trip_gates_can_move_even_if_bump_recovery_reenables() {
        // #439 review: bump_detected's delayed recovery sets the raw can_move bool
        // back to true (it only checks e-stop). can_move() must still report false
        // while the watchdog is tripped, because it gates on watchdog_tripped.
        let (monitor, _rx) = SafetyMonitor::new(SafetyConfig::default());
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        monitor
            .state
            .last_command_ms
            .store(now_ms - 60_000, Ordering::SeqCst);
        assert!(monitor.check_watchdog(Duration::from_secs(30)).await);

        // Simulate bump_detected's recovery task re-enabling the raw bool.
        monitor.state.can_move.store(true, Ordering::SeqCst);
        *monitor.state.block_reason.write().await = None;

        assert!(
            !monitor.can_move().await,
            "watchdog trip must gate can_move() regardless of the raw bool"
        );
    }

    #[tokio::test]
    async fn watchdog_disarmed_before_any_command() {
        // No command issued yet (last_command_ms == 0) => watchdog must not fire,
        // even with a zero timeout.
        let (monitor, _rx) = SafetyMonitor::new(SafetyConfig::default());
        assert!(!monitor.check_watchdog(Duration::from_secs(0)).await);
        assert!(monitor.can_move().await);
    }

    #[tokio::test]
    async fn request_movement_blocked() {
        let config = SafetyConfig {
            min_obstacle_distance: 0.3,
            ..Default::default()
        };
        let (monitor, _rx) = SafetyMonitor::new(config);

        // Set obstacle too close
        monitor.update_obstacle_distance(0.2, 0).await;

        // Movement should be denied
        let result = monitor.request_movement("forward", 1.0).await;
        assert!(result.is_err());
    }

    impl Default for SafetyConfig {
        fn default() -> Self {
            Self {
                min_obstacle_distance: 0.3,
                slow_zone_multiplier: 3.0,
                approach_speed_limit: 0.3,
                max_drive_duration: 30,
                estop_pin: Some(4),
                bump_sensor_pins: vec![5, 6],
                bump_reverse_distance: 0.15,
                confirm_movement: false,
                predict_collisions: true,
                sensor_timeout_secs: 5,
                blind_mode_speed_limit: 0.2,
            }
        }
    }
}
