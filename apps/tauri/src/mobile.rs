//! Mobile entry point for Revka Desktop (iOS/Android).

#[tauri::mobile_entry_point]
fn main() {
    revka_desktop::run();
}
