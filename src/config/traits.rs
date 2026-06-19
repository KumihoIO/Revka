/// The trait for describing a channel
pub trait ChannelConfig {
    /// human-readable name
    fn name() -> &'static str;
    /// short description
    fn desc() -> &'static str;
    /// canonical lowercase channel slug (the TOML key)
    fn slug() -> &'static str;
    /// Configured notification target for this channel instance (if any).
    fn notification_target(&self) -> Option<String> {
        None
    }
    /// Whether this channel supports notification / one-off cold sends.
    fn supports_notify(&self) -> bool {
        false
    }
}

// Maybe there should be a `&self` as parameter for custom channel/info or what...

pub trait ConfigHandle {
    fn name(&self) -> &'static str;
    fn desc(&self) -> &'static str;
    fn slug(&self) -> &'static str;
    /// Configured notification target for this channel instance (if any).
    fn notification_target(&self) -> Option<String> {
        None
    }
    /// Whether this channel supports notification / one-off cold sends.
    fn supports_notify(&self) -> bool {
        false
    }
}
