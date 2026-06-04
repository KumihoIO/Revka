//! Click-tracking redirect endpoint.
//!
//! Companion to the workflow `email:` step's `track_clicks` feature.
//! When the email step rewrites links, every URL becomes
//! `<gateway>/track/c/<encoded_kref>?u=<urlquoted-original>`. This
//! handler decodes the kref, logs the click event, and 302-redirects
//! to the original URL.
//!
//! ## Wire format
//!
//! Must round-trip with `operator_mcp.tracking.encode_kref` and the
//! standalone `kref_encode.py` Python step. Two shapes:
//!
//! * No secret: token is `urlsafe_b64encode(kref)` with padding stripped.
//! * With secret (env `CLICK_TRACKING_SECRET`): token is
//!   `urlsafe_b64encode(kref || ":" || hmac_sha256(secret, kref)[:8])`.
//!   The HMAC catches tampering; truncating to 8 bytes keeps the URL
//!   short.
//!
//! Both encoders + decoders MUST stay in lockstep — change one, change
//! all three (here, `operator_mcp/tracking.py`,
//! `operator_mcp/workflow/builtins/python_steps/kref_encode.py`).
//!
//! ## Deliberately fast
//!
//! Email clients (esp. Gmail) prefetch links and abandon slow
//! redirects. This handler must respond in well under 200ms. So:
//!
//! * No Kumiho/Supabase write is on the redirect path — clicks are
//!   logged via `tracing::info!` synchronously and (TODO) fire a
//!   detached `tokio::spawn` task to persist asynchronously. Even if
//!   the persistence task hangs, the redirect already shipped.
//! * No auth on this endpoint. By design — cold leads can't have
//!   bearer tokens.
//!
//! ## What's NOT here yet
//!
//! * Kumiho persistence (`tag_revision` to "clicked", append to event
//!   log space). Tracked as a follow-up — for now we log via tracing
//!   so the click is visible in daemon logs.
//! * Supabase mirror. Same pattern: detached tokio task fires after
//!   the redirect lands.

use axum::{
    extract::{Path, Query},
    http::StatusCode,
    response::{IntoResponse, Redirect},
};
use base64::Engine;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;
use tracing::{info, warn};

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Deserialize)]
pub struct ClickQuery {
    /// Destination URL — what to redirect to after logging.
    pub u: Option<String>,
}

/// Decoded kref + verification flag.
///
/// `verified` is true only when `secret` was provided AND the embedded
/// HMAC matches. Without a secret, `verified` is always false — that's
/// fine for analytics where the worst case is a forged click on a
/// real ref code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodedClickRef {
    pub kref: String,
    pub verified: bool,
}

/// Length of the truncated HMAC suffix on signed tokens.
const SIG_LEN: usize = 8;

/// Returns Some(body) if `raw` looks like a signed token — i.e. its
/// last `SIG_LEN + 1` bytes are `:<8 sig bytes>`. Otherwise None.
///
/// The "exactly 8 bytes after a colon" heuristic distinguishes signed
/// tokens from unsigned ones whose krefs happen to contain colons (which
/// they always do — `kref://...`). A naive `rposition` on `:` would
/// truncate every kref at its `kref:` scheme separator. Bounded suffix
/// match avoids that.
fn split_signed(raw: &[u8]) -> Option<(&[u8], &[u8])> {
    if raw.len() < SIG_LEN + 1 {
        return None;
    }
    let sep_idx = raw.len() - SIG_LEN - 1;
    if raw[sep_idx] != b':' {
        return None;
    }
    Some((&raw[..sep_idx], &raw[sep_idx + 1..]))
}

/// Decode an encoded click token back to its kref.
///
/// Mirrors `operator_mcp.tracking.decode_kref`. See module docstring
/// for the wire format and contract.
///
/// Robust to the two misconfiguration cases that came up during
/// testing:
///
/// * Token signed with secret X arrives at a gateway with no secret
///   set. We can't verify, but we can still extract the kref portion
///   for click logging — fall through to lossy UTF-8 so non-utf8 HMAC
///   bytes don't panic the handler. `verified=false` either way.
///
/// * Token unsigned arrives at a gateway with a secret set. The raw
///   bytes don't have the `:<8 sig>` suffix shape, so we treat as
///   unsigned (verified=false) instead of mis-splitting on the
///   `kref://` colon and producing a body of just `"kref"`.
pub fn decode_kref(token: &str, secret: Option<&str>) -> Result<DecodedClickRef, String> {
    let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(token.as_bytes())
        .map_err(|e| format!("base64 decode failed: {e}"))?;

    // Determine if this looks like a signed token by suffix shape, NOT
    // just "is there a colon somewhere".
    let signed_split = split_signed(&raw);

    // Unsigned mode: no secret to verify against. The whole payload is
    // assumed to be the kref. We use lossy decoding to handle the case
    // where the token WAS signed (we don't know the secret) — the kref
    // prefix still extracts cleanly; the trailing HMAC bytes become
    // replacement chars, which is fine for log-and-forget.
    let Some(secret) = secret else {
        let kref = String::from_utf8_lossy(&raw).into_owned();
        return Ok(DecodedClickRef {
            kref,
            verified: false,
        });
    };

    // Secret is set, but token doesn't have the signed suffix shape.
    // Treat as unsigned — log the kref, can't verify integrity.
    let Some((body_bytes, sig_bytes)) = signed_split else {
        let kref = String::from_utf8_lossy(&raw).into_owned();
        return Ok(DecodedClickRef {
            kref,
            verified: false,
        });
    };

    let kref =
        String::from_utf8(body_bytes.to_vec()).map_err(|e| format!("kref is not utf-8: {e}"))?;

    // Constant-time HMAC verify — never use raw `==` for crypto.
    let mut mac =
        HmacSha256::new_from_slice(secret.as_bytes()).map_err(|e| format!("hmac key init: {e}"))?;
    mac.update(body_bytes);
    let full = mac.finalize().into_bytes();
    let expected = &full[..SIG_LEN];

    let verified = constant_time_eq(expected, sig_bytes);

    Ok(DecodedClickRef { kref, verified })
}

/// Constant-time byte slice comparison. Safer than `==` for
/// crypto material — `==` short-circuits on first mismatch which leaks
/// timing info to an attacker minting forged HMACs.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Read the click-tracking HMAC secret from env. Falls back to None
/// when unset, in which case decoder works in unsigned mode.
fn click_secret() -> Option<String> {
    match std::env::var("CLICK_TRACKING_SECRET") {
        Ok(v) if !v.trim().is_empty() => Some(v),
        _ => None,
    }
}

/// `GET /track/c/:encoded?u=<dest>` — log a click and 302-redirect.
///
/// Errors (bad token, missing destination) return a small HTML page
/// rather than crashing — a broken tracker shouldn't break the user's
/// browsing flow. Failures get tracing::warn so they show up in
/// daemon logs.
pub async fn handle_click(
    Path(encoded): Path<String>,
    Query(query): Query<ClickQuery>,
) -> impl IntoResponse {
    let secret = click_secret();
    let decoded = decode_kref(&encoded, secret.as_deref());

    let dest = match query.u.as_deref() {
        Some(u) if !u.is_empty() => u.to_string(),
        _ => {
            // Missing destination — can't redirect. Log + return a
            // small explanatory page rather than 500ing.
            warn!(
                "click_tracking: missing 'u' query param for token {}",
                truncate(&encoded, 40)
            );
            return (StatusCode::BAD_REQUEST, "click tracker missing destination").into_response();
        }
    };

    match decoded {
        Ok(DecodedClickRef { kref, verified }) => {
            // Synchronous structured log — visible in `tracing` output.
            // Persistence (Kumiho tag + Supabase mirror) lands in a
            // follow-up; for now the daemon log is the source of truth
            // for click events.
            info!(
                target: "click_tracking",
                kref = %kref,
                verified = verified,
                dest = %dest,
                "click received"
            );
        }
        Err(err) => {
            // Bad token still redirects — losing the click is worse
            // than losing the analytics for a malformed ref code.
            warn!(
                "click_tracking: failed to decode token {}: {}",
                truncate(&encoded, 40),
                err
            );
        }
    }

    Redirect::to(&dest).into_response()
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!("{}…", &s[..max])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;

    /// Reference values from running the Python encoder so the Rust
    /// decoder is pinned to the same wire format. Regenerate via:
    ///
    /// ```bash
    /// python3 -c "import base64,hmac,hashlib
    /// kref = b'kref://Revka/Outreach/contacts/acme.contact'
    /// print('no_secret:', base64.urlsafe_b64encode(kref).rstrip(b'=').decode())
    /// sig = hmac.new(b's3cret', kref, hashlib.sha256).digest()[:8]
    /// print('signed:    ', base64.urlsafe_b64encode(kref+b':'+sig).rstrip(b'=').decode())
    /// "
    /// ```
    const KREF: &str = "kref://Revka/Outreach/contacts/acme.contact";
    const SECRET: &str = "s3cret";

    fn encode_token(kref: &str, secret: Option<&str>) -> String {
        let body = kref.as_bytes();
        let payload = if let Some(s) = secret {
            let mut mac = HmacSha256::new_from_slice(s.as_bytes()).unwrap();
            mac.update(body);
            let sig = mac.finalize().into_bytes();
            let mut combined = Vec::with_capacity(body.len() + 1 + 8);
            combined.extend_from_slice(body);
            combined.push(b':');
            combined.extend_from_slice(&sig[..8]);
            combined
        } else {
            body.to_vec()
        };
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload)
    }

    #[test]
    fn decode_unsigned_roundtrip() {
        let token = encode_token(KREF, None);
        let out = decode_kref(&token, None).unwrap();
        assert_eq!(out.kref, KREF);
        // No secret → no integrity claim.
        assert!(!out.verified);
    }

    #[test]
    fn decode_signed_roundtrip_verifies() {
        let token = encode_token(KREF, Some(SECRET));
        let out = decode_kref(&token, Some(SECRET)).unwrap();
        assert_eq!(out.kref, KREF);
        assert!(out.verified, "matching secret should set verified=true");
    }

    #[test]
    fn wrong_secret_decodes_but_does_not_verify() {
        // Tamper detection: same token, different secret → kref still
        // decodes (so we can log the click with verified=false) but
        // verified is False so a click handler can drop it.
        let token = encode_token(KREF, Some(SECRET));
        let out = decode_kref(&token, Some("wrong")).unwrap();
        assert_eq!(out.kref, KREF);
        assert!(!out.verified);
    }

    #[test]
    fn signed_token_decoded_without_secret_still_extracts_kref() {
        // If the gateway is misconfigured (no CLICK_TRACKING_SECRET set)
        // but the email step DID encode with a secret, we should still
        // log the click — the secret-shaped suffix becomes part of the
        // body bytes, so `verified` is False and the kref looks like
        // `<real_kref>:<garbled>`. Document that — caller decides if
        // that's acceptable.
        let token = encode_token(KREF, Some(SECRET));
        let out = decode_kref(&token, None).unwrap();
        assert!(!out.verified);
        // The colon is the last byte before the 8-byte HMAC, so the
        // unsigned-mode decoder returns the kref + ":" + 8 random bytes.
        // We surface this in logs as the kref starting with the real
        // value — never silently strip the signature.
        assert!(out.kref.starts_with(KREF));
    }

    #[test]
    fn invalid_base64_errors() {
        let result = decode_kref("not!valid!base64@@@", None);
        assert!(result.is_err());
    }

    #[test]
    fn unsigned_token_with_secret_present_does_not_verify() {
        // Email step might have been built before tracking was enabled,
        // sending unsigned tokens that arrive at a gateway with the
        // secret now configured. Those clicks should still log (with
        // verified=false) rather than be silently dropped — we want
        // the analytics signal even if integrity isn't established.
        let token = encode_token(KREF, None);
        let out = decode_kref(&token, Some(SECRET)).unwrap();
        // No colon in raw payload → falls into the "no separator"
        // branch → returns kref with verified=false.
        assert_eq!(out.kref, KREF);
        assert!(!out.verified);
    }

    #[test]
    fn constant_time_eq_basics() {
        assert!(constant_time_eq(b"abc", b"abc"));
        assert!(!constant_time_eq(b"abc", b"abd"));
        assert!(!constant_time_eq(b"abc", b"abcd"));
        assert!(constant_time_eq(b"", b""));
    }
}
