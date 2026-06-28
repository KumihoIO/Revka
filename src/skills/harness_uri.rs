//! Cross-platform `file://` URI formatting for harness artifact locations.
//!
//! When the harness importer registers a discovered skill with Kumiho, the
//! artifact `location` is a `file://` pointer at the **original** harness file —
//! the file is never copied or moved.  The Operator later reads that body
//! *in place* via the operator-mcp loader (`_artifact_path_from_location` in
//! `operator-mcp/operator_mcp/tool_handlers/skills.py`), whose `urlparse`-based
//! decoder only round-trips correctly for the **triple-slash, forward-slash**
//! form of a `file://` URI:
//!
//! - Windows: `file:///G:/git/foo/SKILL.md`
//! - POSIX:   `file:///Users/neo/foo/SKILL.md`
//!
//! [`crate::skills::registration::format_file_uri`] (used for native skills
//! whose content files are POSIX-canonicalised) emits `file://<raw path>` with
//! no normalisation, which decodes incorrectly for Windows harness paths
//! (backslashes, missing leading slash).  Harness files can live anywhere on
//! disk, so the importer uses this module instead.

use std::path::Path;

/// Format an absolute path as a `file://` URI the operator-mcp loader can read.
///
/// Strips Windows verbatim prefixes (`\\?\`, `\\?\UNC\`) left by
/// [`std::fs::canonicalize`], converts `\` to `/`, percent-escapes the few
/// characters that would break URI parsing, and guarantees the triple-slash
/// form for drive-letter and POSIX absolute paths alike.
pub(crate) fn to_file_uri(path: &Path) -> String {
    let raw = path.to_string_lossy();
    let stripped = strip_verbatim_prefix(&raw);
    let forward = stripped.replace('\\', "/");
    let encoded = percent_encode(&forward);
    // Drive-letter paths (`G:/...`) need a leading `/` so the authority is empty
    // (`file:///G:/...`); POSIX paths already start with `/`.
    let rooted = if is_drive_prefixed(&encoded) {
        format!("/{encoded}")
    } else {
        encoded
    };
    format!("file://{rooted}")
}

/// Strip a Windows verbatim (`\\?\`) prefix from a path string.
///
/// `\\?\UNC\server\share` → `\\server\share`; `\\?\G:\x` → `G:\x`.
fn strip_verbatim_prefix(s: &str) -> String {
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else if let Some(rest) = s.strip_prefix(r"\\?\") {
        rest.to_string()
    } else {
        s.to_string()
    }
}

/// True when `s` starts with a Windows drive prefix using forward slashes,
/// e.g. `G:/...` or `c:/...`.
fn is_drive_prefixed(s: &str) -> bool {
    let bytes = s.as_bytes();
    bytes.len() >= 3 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' && bytes[2] == b'/'
}

/// Percent-escape only the characters that would otherwise break `urlparse`
/// (`%` first so the escaping is not itself re-escaped, then `#`/`?`/space).
fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '%' => out.push_str("%25"),
            ' ' => out.push_str("%20"),
            '#' => out.push_str("%23"),
            '?' => out.push_str("%3F"),
            _ => out.push(ch),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Decode a percent-escaped string (generic `%XX`), mirroring Python's
    /// `urllib.parse.unquote` for the cases this module emits.
    fn percent_decode(s: &str) -> String {
        let bytes = s.as_bytes();
        let mut out = Vec::with_capacity(bytes.len());
        let mut i = 0;
        while i < bytes.len() {
            if bytes[i] == b'%' && i + 2 < bytes.len() {
                if let (Some(h), Some(l)) = (hex_val(bytes[i + 1]), hex_val(bytes[i + 2])) {
                    out.push(h * 16 + l);
                    i += 3;
                    continue;
                }
            }
            out.push(bytes[i]);
            i += 1;
        }
        String::from_utf8_lossy(&out).into_owned()
    }

    fn hex_val(b: u8) -> Option<u8> {
        match b {
            b'0'..=b'9' => Some(b - b'0'),
            b'a'..=b'f' => Some(b - b'a' + 10),
            b'A'..=b'F' => Some(b - b'A' + 10),
            _ => None,
        }
    }

    /// Faithful Rust port of operator-mcp `_artifact_path_from_location` for the
    /// triple-slash URIs [`to_file_uri`] produces (authority is always empty, so
    /// `urlparse` puts everything after `file://` into `path`).  This is the
    /// contract the importer MUST satisfy: the operator reads the skill body
    /// from whatever path this returns.
    fn operator_decode(location: &str) -> String {
        let rest = location.strip_prefix("file://").expect("file:// prefix");
        // Triple-slash form => netloc == "", path == rest.
        let decoded = percent_decode(rest);
        // `_WINDOWS_DRIVE_PATH = re.compile(r"^/?[A-Za-z]:[\\/]")` then lstrip("/").
        if is_windows_drive_match(&decoded) {
            decoded.trim_start_matches('/').to_string()
        } else {
            decoded
        }
    }

    /// Mirrors the operator's `^/?[A-Za-z]:[\\/]` regex.
    fn is_windows_drive_match(s: &str) -> bool {
        let s = s.strip_prefix('/').unwrap_or(s);
        let b = s.as_bytes();
        b.len() >= 3
            && b[0].is_ascii_alphabetic()
            && b[1] == b':'
            && (b[2] == b'/' || b[2] == b'\\')
    }

    /// The forward-slash, verbatim-stripped projection of a path — what the
    /// operator should decode our URI back to.
    fn forward_norm(p: &str) -> String {
        strip_verbatim_prefix(p).replace('\\', "/")
    }

    #[test]
    fn windows_drive_path_uses_triple_slash_forward_form() {
        let uri = to_file_uri(Path::new(r"G:\git\KumihoIO\Revka\CLAUDE.md"));
        assert_eq!(uri, "file:///G:/git/KumihoIO/Revka/CLAUDE.md");
    }

    #[test]
    fn windows_verbatim_prefix_is_stripped() {
        let uri = to_file_uri(Path::new(r"\\?\C:\Users\neo\.codex\AGENTS.md"));
        assert_eq!(uri, "file:///C:/Users/neo/.codex/AGENTS.md");
    }

    #[test]
    fn posix_absolute_path_uses_triple_slash() {
        let uri = to_file_uri(Path::new("/Users/neo/.revka/x.md"));
        assert_eq!(uri, "file:///Users/neo/.revka/x.md");
    }

    #[test]
    fn spaces_and_reserved_chars_are_escaped() {
        let uri = to_file_uri(Path::new(r"G:\my repo\a#b?c.md"));
        assert_eq!(uri, "file:///G:/my%20repo/a%23b%3Fc.md");
    }

    #[test]
    fn operator_decodes_windows_uri_back_to_source_path() {
        let p = r"G:\git\KumihoIO\Revka\CLAUDE.md";
        let uri = to_file_uri(Path::new(p));
        assert_eq!(operator_decode(&uri), forward_norm(p));
        assert_eq!(operator_decode(&uri), "G:/git/KumihoIO/Revka/CLAUDE.md");
    }

    #[test]
    fn operator_decodes_posix_uri_back_to_source_path() {
        let p = "/Users/neo/project/.claude/skills/foo/SKILL.md";
        let uri = to_file_uri(Path::new(p));
        assert_eq!(operator_decode(&uri), forward_norm(p));
    }

    #[test]
    fn operator_decodes_escaped_chars_back_to_original() {
        let p = r"G:\my repo\a#b.md";
        let uri = to_file_uri(Path::new(p));
        assert_eq!(operator_decode(&uri), "G:/my repo/a#b.md");
    }

    #[test]
    fn from_file_uri_round_trips() {
        for p in [
            r"G:\git\foo\SKILL.md",
            "/Users/neo/foo/SKILL.md",
            r"G:\my repo\a#b.md",
        ] {
            let uri = to_file_uri(Path::new(p));
            let back = from_file_uri(&uri).expect("round trip");
            assert_eq!(back.to_string_lossy().replace('\\', "/"), forward_norm(p));
        }
    }

    /// Inverse of [`to_file_uri`], used only to assert round-tripping.
    fn from_file_uri(uri: &str) -> Option<PathBuf> {
        let rest = uri.strip_prefix("file://")?;
        let decoded = percent_decode(rest);
        let path = if is_windows_drive_match(&decoded) {
            decoded.trim_start_matches('/').to_string()
        } else {
            decoded
        };
        Some(PathBuf::from(path))
    }
}
