//! Internationalization, two surfaces under one roof:
//!
//! - **Tool descriptions** (`tool_descriptions` submodule, re-exported here)
//!   — TOML-based locale files under `tool_descriptions/<locale>.toml`, used
//!   by the agent runtime when rendering tool surfaces to LLMs.
//! - **Interactive wizards** (`revka onboard` etc.) — Fluent (`.ftl`)
//!   bundles embedded at compile time from `i18n/<lang>/*.ftl`.
//!
//! The two were intentionally split: tool descriptions need many languages
//! (`en`, `zh-CN`, `ja-JP`, …) and live as user-editable TOML files for
//! contributors who don't touch Rust; wizards need a typed enum, plurals,
//! and CLDR-aware formatting for the small set of supported UI languages.
//!
//! # Wizard detection priority
//! 1. CLI flag (`--lang ko`)
//! 2. `REVKA_LANG` environment variable
//! 3. `language` field in `config.toml`
//! 4. POSIX `LC_ALL` / `LANG` (matched on the leading two-letter code)
//! 5. Default: English
//!
//! # Adding a new wizard language
//! 1. Add a variant to [`Lang`] and update `parse` / `id` / `code`.
//! 2. Create `i18n/<code>/onboard.ftl` and embed it in `bundles_for`.
//! 3. Add an option to the language picker in `wizard::setup_language`.

pub mod tool_descriptions;

// Re-export the legacy API so existing call sites
// (`crate::i18n::ToolDescriptions`, `detect_locale`, `default_search_dirs`)
// keep working without import changes.
pub use tool_descriptions::{ToolDescriptions, default_search_dirs, detect_locale};

use fluent_bundle::concurrent::FluentBundle;
use fluent_bundle::{FluentArgs, FluentResource, FluentValue};
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU8, Ordering};
use unic_langid::{LanguageIdentifier, langid};

// ── Language enum ────────────────────────────────────────────────

/// Languages supported by `revka onboard`.
///
/// New variants must be added to `parse`, `id`, `code`, `display_name`,
/// and `bundles_for`. The `.ftl` file under `i18n/<code>/onboard.ftl` is
/// embedded via `include_str!` and missing files are caught at compile time.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    #[default]
    En,
    Ko,
}

impl Lang {
    /// Parse a language tag or alias into a [`Lang`]. Returns `None` for
    /// unsupported codes — callers fall back to the next detection step.
    ///
    /// This is intentionally not `std::str::FromStr` because the standard
    /// trait returns `Result<Self, Self::Err>` and we want a plain
    /// `Option<Self>` for the priority chain in [`detect_lang`].
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "en" | "en-us" | "en_us" | "english" => Some(Lang::En),
            "ko" | "ko-kr" | "ko_kr" | "korean" | "한국어" => Some(Lang::Ko),
            _ => None,
        }
    }

    pub fn id(self) -> LanguageIdentifier {
        match self {
            Lang::En => langid!("en-US"),
            Lang::Ko => langid!("ko-KR"),
        }
    }

    pub fn code(self) -> &'static str {
        match self {
            Lang::En => "en",
            Lang::Ko => "ko",
        }
    }

    pub fn display_name(self) -> &'static str {
        match self {
            Lang::En => "English",
            Lang::Ko => "한국어 (Korean)",
        }
    }

    /// All variants in declaration order. Used by the language picker to build
    /// menu options without listing variants manually.
    pub fn all() -> &'static [Lang] {
        &[Lang::En, Lang::Ko]
    }

    fn as_u8(self) -> u8 {
        match self {
            Lang::En => 0,
            Lang::Ko => 1,
        }
    }

    fn from_u8(n: u8) -> Self {
        match n {
            1 => Lang::Ko,
            _ => Lang::En,
        }
    }
}

// ── Embedded bundles ─────────────────────────────────────────────

const EN_ONBOARD: &str = include_str!("../../i18n/en/onboard.ftl");
const KO_ONBOARD: &str = include_str!("../../i18n/ko/onboard.ftl");

fn bundles_for(lang: Lang) -> &'static [&'static str] {
    match lang {
        Lang::En => &[EN_ONBOARD],
        Lang::Ko => &[KO_ONBOARD],
    }
}

// ── Bundle wrapper ───────────────────────────────────────────────

pub struct I18n {
    bundle: FluentBundle<FluentResource>,
    lang: Lang,
    /// English fallback bundle for keys missing from the active locale.
    fallback: Option<FluentBundle<FluentResource>>,
}

impl I18n {
    pub fn new(lang: Lang) -> Self {
        let bundle = build_bundle(lang);
        let fallback = if matches!(lang, Lang::En) {
            None
        } else {
            Some(build_bundle(Lang::En))
        };
        Self {
            bundle,
            lang,
            fallback,
        }
    }

    pub fn lang(&self) -> Lang {
        self.lang
    }

    pub fn t(&self, key: &str) -> String {
        self.t_with(key, None)
    }

    pub fn t_args(&self, key: &str, args: &FluentArgs) -> String {
        self.t_with(key, Some(args))
    }

    fn t_with(&self, key: &str, args: Option<&FluentArgs>) -> String {
        if let Some(s) = format_message(&self.bundle, key, args) {
            return s;
        }
        if let Some(fallback) = &self.fallback {
            if let Some(s) = format_message(fallback, key, args) {
                return s;
            }
        }
        // Last-resort: return the key so missing strings are visible in dev
        // rather than silently swallowed.
        key.to_string()
    }
}

fn build_bundle(lang: Lang) -> FluentBundle<FluentResource> {
    let mut bundle = FluentBundle::new_concurrent(vec![lang.id()]);
    // Fluent inserts U+2068/U+2069 isolate marks around args by default.
    // Disable for terminal output where those bytes render as garbage on
    // CJK-aware shells but blank squares on others.
    bundle.set_use_isolating(false);
    for src in bundles_for(lang) {
        let resource = FluentResource::try_new((*src).to_string())
            .expect("translation bundle has invalid Fluent syntax — fix at compile time");
        bundle
            .add_resource(resource)
            .expect("duplicate keys in translation bundle");
    }
    bundle
}

fn format_message(
    bundle: &FluentBundle<FluentResource>,
    key: &str,
    args: Option<&FluentArgs>,
) -> Option<String> {
    let msg = bundle.get_message(key)?;
    let pattern = msg.value()?;
    let mut errors = vec![];
    let s = bundle
        .format_pattern(pattern, args, &mut errors)
        .into_owned();
    if !errors.is_empty() {
        // Don't crash on missing args — degrade to the formatted string with
        // {$placeholder} verbatim. Surface in tracing for ops to spot.
        tracing::debug!(?errors, key, "fluent format errors");
    }
    Some(s)
}

// ── Detection ────────────────────────────────────────────────────

/// Resolve the active language from CLI flag, env, config, and POSIX locale,
/// in that priority order. Returns [`Lang::En`] if nothing matches.
pub fn detect_lang(cli_flag: Option<&str>, config_lang: Option<&str>) -> Lang {
    if let Some(s) = cli_flag {
        if let Some(l) = Lang::parse(s) {
            return l;
        }
    }
    if let Ok(s) = std::env::var("REVKA_LANG") {
        if let Some(l) = Lang::parse(&s) {
            return l;
        }
    }
    if let Some(s) = config_lang {
        if let Some(l) = Lang::parse(s) {
            return l;
        }
    }
    for var in ["LC_ALL", "LANG"] {
        if let Ok(s) = std::env::var(var) {
            if let Some(l) = lang_from_locale(&s) {
                return l;
            }
        }
    }
    Lang::En
}

/// Match a POSIX locale string (e.g. `ko_KR.UTF-8`, `en_US`) on its leading
/// two-letter code. Case-insensitive.
fn lang_from_locale(s: &str) -> Option<Lang> {
    let head: String = s.chars().take(2).collect();
    Lang::parse(&head)
}

// ── Global handle ────────────────────────────────────────────────
//
// Bundles are cached per-language in `OnceLock`s so they're built lazily on
// first use and reused for the rest of the process. The active language is
// stored in an `AtomicU8` so the wizard can switch languages mid-flow (the
// Step 0 picker) without locking. Switching is rare; lookups are hot.

static EN_BUNDLE: OnceLock<I18n> = OnceLock::new();
static KO_BUNDLE: OnceLock<I18n> = OnceLock::new();
static ACTIVE: AtomicU8 = AtomicU8::new(0); // 0 = En, 1 = Ko

fn bundle_for(lang: Lang) -> &'static I18n {
    match lang {
        Lang::En => EN_BUNDLE.get_or_init(|| I18n::new(Lang::En)),
        Lang::Ko => KO_BUNDLE.get_or_init(|| I18n::new(Lang::Ko)),
    }
}

/// Initialize the global i18n state with the active language. Equivalent to
/// [`set_lang`] but conventionally called once at startup.
pub fn init(lang: Lang) {
    set_lang(lang);
}

/// Switch the active language at runtime. The Step 0 wizard picker uses this
/// after the user makes a selection so the rest of the wizard renders in the
/// chosen language.
pub fn set_lang(lang: Lang) {
    ACTIVE.store(lang.as_u8(), Ordering::Release);
}

/// Return the currently active language.
pub fn lang() -> Lang {
    Lang::from_u8(ACTIVE.load(Ordering::Acquire))
}

/// Look up a translation key in the active language. Falls back to English,
/// then to the key itself if the message is missing everywhere.
pub fn t(key: &str) -> String {
    bundle_for(lang()).t(key)
}

/// Look up a translation key with named arguments. Same fallback semantics as
/// [`t`].
pub fn t_args(key: &str, args: &FluentArgs) -> String {
    bundle_for(lang()).t_args(key, args)
}

// ── Macros ───────────────────────────────────────────────────────

/// Convenience macro: `t!("welcome")` or `t!("step-header", num = 1, total = 9, title = "Workspace Setup")`.
///
/// String, integer, and float values are accepted as Fluent argument values.
#[macro_export]
macro_rules! t {
    ($key:expr) => { $crate::i18n::t($key) };
    ($key:expr, $($name:ident = $value:expr),+ $(,)?) => {{
        let mut args = ::fluent_bundle::FluentArgs::new();
        $( args.set(stringify!($name), $crate::i18n::IntoFluentValue::into_fluent_value($value)); )+
        $crate::i18n::t_args($key, &args)
    }};
}

// ── FluentValue conversion helper ────────────────────────────────

/// Tiny shim so the `t!` macro accepts both `&str`, `String`, and integer types
/// without callers having to wrap each argument in `FluentValue::from`.
pub trait IntoFluentValue {
    fn into_fluent_value(self) -> FluentValue<'static>;
}

impl IntoFluentValue for String {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(self)
    }
}

impl IntoFluentValue for &str {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(self.to_string())
    }
}

impl IntoFluentValue for &String {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(self.clone())
    }
}

impl IntoFluentValue for u16 {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(i64::from(self))
    }
}

impl IntoFluentValue for u32 {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(i64::from(self))
    }
}

impl IntoFluentValue for u64 {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(self as i64)
    }
}

impl IntoFluentValue for i32 {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(i64::from(self))
    }
}

impl IntoFluentValue for i64 {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(self)
    }
}

impl IntoFluentValue for usize {
    fn into_fluent_value(self) -> FluentValue<'static> {
        FluentValue::from(self as i64)
    }
}

// ── Tests ────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn from_str_handles_common_aliases() {
        assert_eq!(Lang::parse("en"), Some(Lang::En));
        assert_eq!(Lang::parse("EN"), Some(Lang::En));
        assert_eq!(Lang::parse("en-US"), Some(Lang::En));
        assert_eq!(Lang::parse("english"), Some(Lang::En));
        assert_eq!(Lang::parse("ko"), Some(Lang::Ko));
        assert_eq!(Lang::parse("ko-KR"), Some(Lang::Ko));
        assert_eq!(Lang::parse("ko_KR"), Some(Lang::Ko));
        assert_eq!(Lang::parse("korean"), Some(Lang::Ko));
        assert_eq!(Lang::parse("한국어"), Some(Lang::Ko));
        assert_eq!(Lang::parse("ja"), None);
        assert_eq!(Lang::parse(""), None);
    }

    #[test]
    fn locale_string_matched_on_two_letter_prefix() {
        assert_eq!(lang_from_locale("ko_KR.UTF-8"), Some(Lang::Ko));
        assert_eq!(lang_from_locale("en_US.UTF-8"), Some(Lang::En));
        assert_eq!(lang_from_locale("en"), Some(Lang::En));
        assert_eq!(lang_from_locale("ja_JP.UTF-8"), None);
    }

    #[test]
    fn english_bundle_resolves_known_key() {
        let i = I18n::new(Lang::En);
        // welcome-title is one of the Phase 1 keys — if this changes, update
        // the assertion to whatever the new English source says.
        assert_eq!(i.t("welcome-title"), "Welcome to the Revka.");
    }

    #[test]
    fn korean_bundle_resolves_known_key() {
        let i = I18n::new(Lang::Ko);
        // The Korean translation of welcome-title — kept in sync with
        // i18n/ko/onboard.ftl.
        assert_eq!(i.t("welcome-title"), "Revka에 오신 것을 환영합니다.");
    }

    #[test]
    fn missing_key_returns_the_key_itself() {
        let en = I18n::new(Lang::En);
        assert_eq!(en.t("nonexistent-key"), "nonexistent-key");
        let ko = I18n::new(Lang::Ko);
        assert_eq!(ko.t("nonexistent-key"), "nonexistent-key");
    }

    #[test]
    fn args_substitute_into_pattern() {
        let i = I18n::new(Lang::En);
        let mut args = FluentArgs::new();
        args.set("path", "/tmp/foo");
        let s = i.t_args("workspace-confirmed", &args);
        assert!(s.contains("/tmp/foo"), "rendered string was: {s:?}");
    }
}
