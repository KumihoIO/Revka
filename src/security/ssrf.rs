//! Shared SSRF guards: a private/reserved-IP deny-list and a reqwest DNS
//! resolver that enforces it **at connect time**.
//!
//! Validating the resolved IPs at connect time (via [`SsrfResolver`]) — rather
//! than only checking the URL string up front — closes the DNS-rebinding
//! time-of-check/time-of-use gap and covers every redirect hop, not just the
//! originally-requested URL.
//!
//! Canonical home for SSRF resolution. The link enricher uses it; `web_fetch`
//! and `http_request` carry their own (older) copies of this logic and should
//! be migrated onto this module in a follow-up so the checks cannot drift.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::sync::Arc;

use anyhow::Result;

/// True if an IPv4 address is private/reserved (non-global) and so must not be
/// an outbound connection target.
pub(crate) fn is_non_global_v4(v4: Ipv4Addr) -> bool {
    let [a, b, c, _] = v4.octets();
    v4.is_loopback()
        || v4.is_private()
        || v4.is_link_local()
        || v4.is_unspecified()
        || v4.is_broadcast()
        || v4.is_multicast()
        || (a == 100 && (64..=127).contains(&b)) // 100.64.0.0/10 CGNAT
        || a >= 240 // 240.0.0.0/4 reserved
        || (a == 192 && b == 0 && (c == 0 || c == 2)) // 192.0.0.0/24, 192.0.2.0/24
        || (a == 198 && b == 51) // 198.51.100.0/24 (TEST-NET-2)
        || (a == 203 && b == 0) // 203.0.113.0/24 (TEST-NET-3)
        || (a == 198 && (18..=19).contains(&b)) // 198.18.0.0/15 benchmarking
}

/// True if an IPv6 address is private/reserved (non-global).
pub(crate) fn is_non_global_v6(v6: Ipv6Addr) -> bool {
    let segs = v6.segments();
    v6.is_loopback()
        || v6.is_unspecified()
        || v6.is_multicast()
        || (segs[0] & 0xfe00) == 0xfc00 // fc00::/7 unique-local
        || (segs[0] & 0xffc0) == 0xfe80 // fe80::/10 link-local
        || (segs[0] == 0x2001 && segs[1] == 0x0db8) // 2001:db8::/32 documentation
        // Catches both IPv4-mapped (::ffff:a.b.c.d) and deprecated
        // IPv4-compatible (::a.b.c.d) embeddings, e.g. ::169.254.169.254.
        || v6.to_ipv4().is_some_and(is_non_global_v4)
}

/// True if an IP is private/reserved (non-global).
pub(crate) fn is_non_global_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => is_non_global_v4(v4),
        IpAddr::V6(v6) => is_non_global_v6(v6),
    }
}

/// Validate that a resolved host's IPs are all public; bail on the first
/// non-global address (or if the host resolved to no address).
pub(crate) fn validate_public_ips(host: &str, ips: &[IpAddr]) -> Result<()> {
    if ips.is_empty() {
        anyhow::bail!("Failed to resolve host '{host}'");
    }
    for ip in ips {
        if is_non_global_ip(*ip) {
            anyhow::bail!("Blocked host '{host}' resolved to non-global address {ip}");
        }
    }
    Ok(())
}

/// Predicate deciding whether a host may bypass the deny-list (operator opt-in).
pub(crate) type AllowHost = Arc<dyn Fn(&str) -> bool + Send + Sync>;

/// Resolve `host` and validate every resulting IP against the deny-list (unless
/// `allow_host` permits the host), returning the socket addresses to connect to.
pub(crate) async fn resolve_validated(
    host: &str,
    allow_host: &AllowHost,
) -> Result<Vec<SocketAddr>> {
    use std::net::ToSocketAddrs;
    let lookup = host.to_string();
    let addrs: Vec<SocketAddr> = tokio::task::spawn_blocking(move || {
        (lookup.as_str(), 0)
            .to_socket_addrs()
            .map(|it| it.collect::<Vec<_>>())
    })
    .await
    .map_err(|e| anyhow::anyhow!("DNS resolve task failed for '{host}': {e}"))?
    .map_err(|e| anyhow::anyhow!("Failed to resolve host '{host}': {e}"))?;

    // Match opt-in hosts on the FQDN root (without a trailing dot).
    let bare = host.strip_suffix('.').unwrap_or(host);
    if !allow_host(bare) {
        let ips: Vec<IpAddr> = addrs.iter().map(|a| a.ip()).collect();
        validate_public_ips(host, &ips)?;
    }
    Ok(addrs)
}

/// A reqwest base DNS resolver that rejects connections resolving to
/// private/reserved IPs (SSRF guard) at connect time — covering the original
/// host, every redirect hop, and any host an exact pin override does not match.
/// Hosts for which `allow_host` returns true bypass the deny-list.
pub(crate) struct SsrfResolver {
    allow_host: AllowHost,
}

impl SsrfResolver {
    pub(crate) fn new(allow_host: AllowHost) -> Self {
        Self { allow_host }
    }

    /// A resolver that rejects all private/reserved IPs (no opt-in hosts).
    pub(crate) fn deny_private() -> Self {
        Self::new(Arc::new(|_| false))
    }
}

impl reqwest::dns::Resolve for SsrfResolver {
    fn resolve(&self, name: reqwest::dns::Name) -> reqwest::dns::Resolving {
        let host = name.as_str().to_string();
        let allow = self.allow_host.clone();
        Box::pin(async move {
            let addrs = resolve_validated(&host, &allow).await.map_err(|e| {
                Box::new(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    e.to_string(),
                )) as Box<dyn std::error::Error + Send + Sync>
            })?;
            Ok(Box::new(addrs.into_iter()) as Box<dyn Iterator<Item = SocketAddr> + Send>)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deny_list_covers_private_and_reserved() {
        for ip in [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254", // cloud metadata
            "100.64.0.1",      // CGNAT
            "::1",
            "::ffff:127.0.0.1",  // IPv4-mapped loopback
            "::169.254.169.254", // IPv4-compatible metadata
            "fe80::1",           // link-local
            "fc00::1",           // unique-local
        ] {
            assert!(
                is_non_global_ip(ip.parse().unwrap()),
                "{ip} should be non-global"
            );
        }
        for ip in ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"] {
            assert!(
                !is_non_global_ip(ip.parse().unwrap()),
                "{ip} should be global"
            );
        }
    }

    #[test]
    fn validate_public_ips_rejects_any_private() {
        assert!(validate_public_ips("h", &["1.1.1.1".parse().unwrap()]).is_ok());
        assert!(
            validate_public_ips(
                "h",
                &["1.1.1.1".parse().unwrap(), "10.0.0.1".parse().unwrap()]
            )
            .is_err()
        );
        assert!(validate_public_ips("h", &[]).is_err());
    }

    #[tokio::test]
    async fn resolver_rejects_loopback() {
        let deny: AllowHost = Arc::new(|_| false);
        assert!(resolve_validated("localhost", &deny).await.is_err());
    }

    #[tokio::test]
    async fn resolver_allows_opted_in_host() {
        let allow: AllowHost = Arc::new(|h| h == "localhost");
        assert!(
            !resolve_validated("localhost", &allow)
                .await
                .unwrap()
                .is_empty()
        );
    }
}
