use crate::config::OtpConfig;
use crate::security::pairing::constant_time_eq;
use crate::security::secrets::SecretStore;
use anyhow::{Context, Result};
use parking_lot::Mutex;
use ring::hmac;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const OTP_SECRET_FILE: &str = "otp-secret";
const OTP_DIGITS: u32 = 6;
const OTP_ISSUER: &str = "Revka";

#[derive(Debug)]
pub struct OtpValidator {
    config: OtpConfig,
    secret: Vec<u8>,
    /// Codes that have already validated once, keyed by a fixed-length
    /// SHA-256 digest of `(code, counter)` and mapped to the timestamp after
    /// which they may be forgotten. A code present here is treated as consumed
    /// and is rejected on any subsequent presentation, making each code
    /// single-use rather than replayable within `cache_valid_secs`. The key is
    /// a digest rather than the plaintext code so the `HashMap` lookup never
    /// hashes/compares the raw secret and cannot be probed via lookup timing.
    consumed_codes: Mutex<HashMap<[u8; 32], u64>>,
}

impl OtpValidator {
    pub fn from_config(
        config: &OtpConfig,
        revka_dir: &Path,
        store: &SecretStore,
    ) -> Result<(Self, Option<String>)> {
        let secret_path = secret_file_path(revka_dir);
        let (secret, generated) = if secret_path.exists() {
            let encoded = fs::read_to_string(&secret_path).with_context(|| {
                format!("Failed to read OTP secret file {}", secret_path.display())
            })?;
            let decrypted = store
                .decrypt(encoded.trim())
                .context("Failed to decrypt OTP secret file")?;
            (decode_base32_secret(&decrypted)?, false)
        } else {
            let raw: [u8; 20] = rand::random();
            let encoded_secret = encode_base32_secret(&raw);
            let encrypted = store
                .encrypt(&encoded_secret)
                .context("Failed to encrypt OTP secret")?;
            write_secret_file(&secret_path, &encrypted)?;
            (raw.to_vec(), true)
        };

        let validator = Self {
            config: config.clone(),
            secret,
            consumed_codes: Mutex::new(HashMap::new()),
        };
        let uri = if generated {
            Some(validator.otpauth_uri())
        } else {
            None
        };
        Ok((validator, uri))
    }

    pub fn validate(&self, code: &str) -> Result<bool> {
        self.validate_at(code, unix_timestamp_now())
    }

    fn validate_at(&self, code: &str, now_secs: u64) -> Result<bool> {
        let normalized = code.trim();
        if normalized.len() != OTP_DIGITS as usize
            || !normalized.chars().all(|ch| ch.is_ascii_digit())
        {
            return Ok(false);
        }

        let step = self.config.token_ttl_secs.max(1);
        let counter = now_secs / step;
        let counters = [
            counter.saturating_sub(1),
            counter,
            counter.saturating_add(1),
        ];

        {
            // A code that has already validated once is consumed: reject any
            // later presentation so an intercepted code cannot be replayed
            // within `cache_valid_secs`. Stale entries are pruned first so the
            // map cannot grow unbounded. Lookups are keyed on the digest of
            // each candidate `(code, counter)` so the `HashMap` never operates
            // on the plaintext code.
            let mut consumed = self.consumed_codes.lock();
            consumed.retain(|_, expiry| *expiry >= now_secs);
            let already_consumed = counters.iter().any(|c| {
                consumed
                    .get(&consumed_key(normalized, *c))
                    .is_some_and(|expiry| *expiry >= now_secs)
            });
            if already_consumed {
                return Ok(false);
            }
        }

        // Compare the supplied code against every candidate in constant time,
        // accumulating the result so the loop does not short-circuit on the
        // first match and leak which counter window produced the code via
        // timing. The matched counter is recorded so the consumed entry can be
        // keyed on the digest of `(code, counter)`.
        let mut is_valid = false;
        let mut matched_counter = counter;
        for c in counters {
            let candidate = compute_totp_code(&self.secret, c);
            let matches = constant_time_eq(&candidate, normalized);
            if matches {
                matched_counter = c;
            }
            is_valid |= matches;
        }

        if is_valid {
            // Remember this code as consumed so it cannot be replayed. The
            // entry is retained for `cache_valid_secs` to keep rejecting
            // replays past the TOTP step that produced the code.
            let mut consumed = self.consumed_codes.lock();
            consumed.insert(
                consumed_key(normalized, matched_counter),
                now_secs.saturating_add(self.config.cache_valid_secs),
            );
        }

        Ok(is_valid)
    }

    pub fn otpauth_uri(&self) -> String {
        let secret = encode_base32_secret(&self.secret);
        let account = "revka";
        format!(
            "otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&period={period}",
            issuer = OTP_ISSUER,
            period = self.config.token_ttl_secs.max(1)
        )
    }

    #[cfg(test)]
    pub(crate) fn code_for_timestamp(&self, timestamp: u64) -> String {
        let counter = timestamp / self.config.token_ttl_secs.max(1);
        compute_totp_code(&self.secret, counter)
    }
}

pub fn secret_file_path(revka_dir: &Path) -> PathBuf {
    revka_dir.join(OTP_SECRET_FILE)
}

fn write_secret_file(path: &Path, value: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("Failed to create directory {}", parent.display()))?;
    }

    let temp_path = path.with_extension(format!("tmp-{}", uuid::Uuid::new_v4()));
    fs::write(&temp_path, value).with_context(|| {
        format!(
            "Failed to write temporary OTP secret {}",
            temp_path.display()
        )
    })?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&temp_path, fs::Permissions::from_mode(0o600));
    }

    fs::rename(&temp_path, path).with_context(|| {
        format!(
            "Failed to atomically replace OTP secret file {}",
            path.display()
        )
    })?;
    Ok(())
}

fn unix_timestamp_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

/// Fixed-length key for the consumed-codes cache: a SHA-256 digest of the
/// `(code, counter)` pair. Keying on a digest rather than the plaintext code
/// keeps the raw secret out of the `HashMap` and gives every key the same
/// length, so neither hashing nor equality on a probe can leak information
/// about a real code via lookup timing.
fn consumed_key(code: &str, counter: u64) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(code.as_bytes());
    hasher.update(counter.to_be_bytes());
    hasher.finalize().into()
}

fn compute_totp_code(secret: &[u8], counter: u64) -> String {
    let key = hmac::Key::new(hmac::HMAC_SHA1_FOR_LEGACY_USE_ONLY, secret);
    let counter_bytes = counter.to_be_bytes();
    let digest = hmac::sign(&key, &counter_bytes);
    let hash = digest.as_ref();

    let offset = (hash[19] & 0x0f) as usize;
    let binary = ((u32::from(hash[offset]) & 0x7f) << 24)
        | (u32::from(hash[offset + 1]) << 16)
        | (u32::from(hash[offset + 2]) << 8)
        | u32::from(hash[offset + 3]);

    let code = binary % 10_u32.pow(OTP_DIGITS);
    format!("{code:0>6}")
}

fn encode_base32_secret(input: &[u8]) -> String {
    const ALPHABET: &[u8; 32] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    if input.is_empty() {
        return String::new();
    }

    let mut result = String::new();
    let mut buffer = 0u16;
    let mut bits_left = 0u8;

    for byte in input {
        buffer = (buffer << 8) | u16::from(*byte);
        bits_left += 8;

        while bits_left >= 5 {
            let index = ((buffer >> (bits_left - 5)) & 0x1f) as usize;
            result.push(ALPHABET[index] as char);
            bits_left -= 5;
        }
    }

    if bits_left > 0 {
        let index = ((buffer << (5 - bits_left)) & 0x1f) as usize;
        result.push(ALPHABET[index] as char);
    }

    result
}

fn decode_base32_secret(raw: &str) -> Result<Vec<u8>> {
    fn decode_char(ch: char) -> Option<u8> {
        match ch {
            'A'..='Z' => Some((ch as u8) - b'A'),
            '2'..='7' => Some((ch as u8) - b'2' + 26),
            _ => None,
        }
    }

    let mut cleaned = raw
        .chars()
        .filter(|ch| !matches!(ch, ' ' | '\t' | '\n' | '\r' | '-'))
        .collect::<String>()
        .to_ascii_uppercase();
    while cleaned.ends_with('=') {
        cleaned.pop();
    }
    if cleaned.is_empty() {
        anyhow::bail!("OTP secret is empty");
    }

    let mut output = Vec::new();
    let mut buffer = 0u32;
    let mut bits_left = 0u8;

    for ch in cleaned.chars() {
        let value = decode_char(ch)
            .with_context(|| format!("OTP secret contains invalid base32 character '{ch}'"))?;
        buffer = (buffer << 5) | u32::from(value);
        bits_left += 5;

        if bits_left >= 8 {
            let byte = ((buffer >> (bits_left - 8)) & 0xff) as u8;
            output.push(byte);
            bits_left -= 8;
        }
    }

    if output.is_empty() {
        anyhow::bail!("OTP secret did not decode to any bytes");
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn test_config() -> OtpConfig {
        OtpConfig {
            enabled: true,
            token_ttl_secs: 30,
            cache_valid_secs: 120,
            ..OtpConfig::default()
        }
    }

    #[test]
    fn valid_totp_code_is_accepted() {
        let dir = tempdir().unwrap();
        let store = SecretStore::new(dir.path(), true);
        let (validator, _) = OtpValidator::from_config(&test_config(), dir.path(), &store).unwrap();

        let now = 1_700_000_000u64;
        let code = validator.code_for_timestamp(now);
        assert!(validator.validate_at(&code, now).unwrap());
    }

    #[test]
    fn expired_totp_code_is_rejected() {
        let dir = tempdir().unwrap();
        let store = SecretStore::new(dir.path(), true);
        let (validator, _) = OtpValidator::from_config(&test_config(), dir.path(), &store).unwrap();

        let stale = 1_700_000_000u64;
        let now = stale + 300;
        let code = validator.code_for_timestamp(stale);
        assert!(!validator.validate_at(&code, now).unwrap());
    }

    #[test]
    fn validated_code_cannot_be_replayed_within_cache_window() {
        let dir = tempdir().unwrap();
        let store = SecretStore::new(dir.path(), true);
        let (validator, _) = OtpValidator::from_config(&test_config(), dir.path(), &store).unwrap();

        let now = 1_700_000_000u64;
        let code = validator.code_for_timestamp(now);

        // First presentation succeeds and consumes the code.
        assert!(validator.validate_at(&code, now).unwrap());
        // A replay within the still-valid TOTP step is rejected.
        assert!(!validator.validate_at(&code, now).unwrap());
        // A later replay still inside `cache_valid_secs` is also rejected.
        assert!(!validator.validate_at(&code, now + 60).unwrap());
    }

    #[test]
    fn wrong_totp_code_is_rejected() {
        let dir = tempdir().unwrap();
        let store = SecretStore::new(dir.path(), true);
        let (validator, _) = OtpValidator::from_config(&test_config(), dir.path(), &store).unwrap();
        assert!(!validator.validate_at("123456", 1_700_000_000).unwrap());
    }

    #[test]
    fn secret_is_generated_and_reused() {
        let dir = tempdir().unwrap();
        let store = SecretStore::new(dir.path(), true);

        let (first, first_uri) =
            OtpValidator::from_config(&test_config(), dir.path(), &store).unwrap();
        assert!(first_uri.is_some());

        let secret_path = secret_file_path(dir.path());
        let stored = fs::read_to_string(&secret_path).unwrap();
        assert!(SecretStore::is_encrypted(stored.trim()));

        let (second, second_uri) =
            OtpValidator::from_config(&test_config(), dir.path(), &store).unwrap();
        assert!(second_uri.is_none());

        let ts = 1_700_000_000u64;
        assert_eq!(first.code_for_timestamp(ts), second.code_for_timestamp(ts));
    }
}
