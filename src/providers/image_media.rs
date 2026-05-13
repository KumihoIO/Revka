use base64::Engine as _;

pub(crate) fn sniff_image_media_type(bytes: &[u8]) -> Option<&'static str> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some("image/png")
    } else if bytes.starts_with(b"\xff\xd8\xff") {
        Some("image/jpeg")
    } else if bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a") {
        Some("image/gif")
    } else if bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP" {
        Some("image/webp")
    } else {
        None
    }
}

pub(crate) fn decode_base64_header(data: &str) -> Option<Vec<u8>> {
    let compact: String = data
        .chars()
        .filter(|c| !c.is_whitespace())
        .take(96)
        .collect();
    if compact.is_empty() {
        return None;
    }
    let mut padded = compact;
    while !padded.len().is_multiple_of(4) {
        padded.push('=');
    }
    base64::engine::general_purpose::STANDARD
        .decode(padded)
        .ok()
}

pub(crate) fn image_media_type_from_data_uri(header: &str, data: &str) -> String {
    let declared = header
        .split(';')
        .next()
        .filter(|mime| mime.starts_with("image/"))
        .unwrap_or("image/jpeg");
    decode_base64_header(data)
        .as_deref()
        .and_then(sniff_image_media_type)
        .unwrap_or(declared)
        .to_string()
}

pub(crate) fn image_media_type_from_path(path: &std::path::Path, bytes: &[u8]) -> String {
    if let Some(sniffed) = sniff_image_media_type(bytes) {
        return sniffed.to_string();
    }

    match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("jpg")
        .to_ascii_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "gif" => "image/gif",
        "webp" => "image/webp",
        _ => "image/jpeg",
    }
    .to_string()
}

pub(crate) fn bedrock_image_format_from_media_type(media_type: &str) -> &'static str {
    match media_type {
        "image/png" => "png",
        "image/gif" => "gif",
        "image/webp" => "webp",
        _ => "jpeg",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_uri_media_type_prefers_sniffed_bytes() {
        let png_header = "iVBORw0KGgo=";
        assert_eq!(
            image_media_type_from_data_uri("image/jpeg;base64", png_header),
            "image/png"
        );
    }

    #[test]
    fn data_uri_media_type_falls_back_to_declared_type() {
        assert_eq!(
            image_media_type_from_data_uri("image/webp;base64", "not-image"),
            "image/webp"
        );
    }
}
