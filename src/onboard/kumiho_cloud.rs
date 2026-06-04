use anyhow::{Context, Result, bail};
use serde::Deserialize;
use serde_json::{Value, json};
use std::time::Duration;

pub const DEFAULT_KUMIHO_WEB_URL: &str = "https://kumiho.io";

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OnboardingConfig {
    pub firebase: FirebaseConfig,
    pub endpoints: OnboardingEndpoints,
    pub plans: Vec<PlanOption>,
    pub regions: Vec<RegionOption>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FirebaseConfig {
    pub api_key: String,
    #[allow(dead_code)]
    pub project_id: String,
    #[allow(dead_code)]
    pub auth_domain: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OnboardingEndpoints {
    pub signup: String,
    pub checkout_session: String,
    pub tenant: String,
    pub control_plane: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanOption {
    pub code: String,
    pub label: String,
    pub requires_checkout: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RegionOption {
    pub code: String,
    pub label: String,
    #[allow(dead_code)]
    pub server_url: String,
    #[allow(dead_code)]
    pub grpc_authority: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FirebaseSession {
    pub id_token: String,
    pub refresh_token: Option<String>,
    pub local_id: Option<String>,
    pub email: Option<String>,
}

#[derive(Debug, Clone)]
pub struct FirebaseAuthOutcome {
    pub session: FirebaseSession,
    pub created_account: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CheckoutSession {
    pub checkout_url: String,
    pub session_id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SignupResult {
    pub tenant_id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ServiceTokenResult {
    pub token: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TenantInfo {
    pub tenant_id: Option<String>,
    pub control_plane_url: Option<String>,
}

pub struct KumihoCloudClient {
    base_url: String,
    http: reqwest::blocking::Client,
}

impl KumihoCloudClient {
    pub fn new(base_url: &str) -> Result<Self> {
        let http = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .context("failed to build Kumiho Cloud HTTP client")?;
        Ok(Self {
            base_url: normalize_base_url(base_url),
            http,
        })
    }

    pub fn from_env() -> Result<Self> {
        let base_url = std::env::var("REVKA_KUMIHO_WEB_URL")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| DEFAULT_KUMIHO_WEB_URL.to_string());
        Self::new(&base_url)
    }

    pub fn fetch_config(&self) -> Result<OnboardingConfig> {
        let url = format!("{}/api/revka/onboarding/config", self.base_url);
        let response = self
            .http
            .get(&url)
            .send()
            .with_context(|| format!("failed to reach Kumiho onboarding config at {url}"))?;
        decode_response(response, "Kumiho onboarding config")
    }

    pub fn sign_up_or_sign_in(
        &self,
        firebase: &FirebaseConfig,
        email: &str,
        password: &str,
    ) -> Result<FirebaseAuthOutcome> {
        match self.firebase_password_request("accounts:signUp", firebase, email, password) {
            Ok(session) => Ok(FirebaseAuthOutcome {
                session,
                created_account: true,
            }),
            Err(error) if error.to_string().contains("EMAIL_EXISTS") => {
                let session = self.firebase_password_request(
                    "accounts:signInWithPassword",
                    firebase,
                    email,
                    password,
                )?;
                Ok(FirebaseAuthOutcome {
                    session,
                    created_account: false,
                })
            }
            Err(error) => Err(error),
        }
    }

    pub fn update_display_name(
        &self,
        firebase: &FirebaseConfig,
        id_token: &str,
        display_name: &str,
    ) -> Result<FirebaseSession> {
        let url = firebase_auth_url("accounts:update", &firebase.api_key);
        let response = self
            .http
            .post(url)
            .json(&json!({
                "idToken": id_token,
                "displayName": display_name,
                "returnSecureToken": true
            }))
            .send()
            .context("failed to update Firebase profile")?;
        decode_response(response, "Firebase profile update")
    }

    pub fn create_checkout_session(
        &self,
        endpoints: &OnboardingEndpoints,
        id_token: &str,
        plan: &str,
        region: &str,
        organization_name: Option<&str>,
    ) -> Result<CheckoutSession> {
        let response = self
            .http
            .post(&endpoints.checkout_session)
            .json(&json!({
                "idToken": id_token,
                "plan": plan,
                "memoryAddOn": "none",
                "region": region,
                "organizationName": organization_name
            }))
            .send()
            .context("failed to create Kumiho checkout session")?;
        decode_response(response, "Kumiho checkout session")
    }

    pub fn create_signup(
        &self,
        endpoints: &OnboardingEndpoints,
        id_token: &str,
        plan: &str,
        region: &str,
        display_name: Option<&str>,
        organization_name: Option<&str>,
        checkout_session_id: Option<&str>,
    ) -> Result<SignupResult> {
        let response = self
            .http
            .post(&endpoints.signup)
            .json(&json!({
                "idToken": id_token,
                "plan": plan,
                "memoryAddOn": "none",
                "region": region,
                "displayName": display_name,
                "organizationName": organization_name,
                "stripeCheckoutSessionId": checkout_session_id
            }))
            .send()
            .context("failed to create Kumiho signup")?;
        decode_response(response, "Kumiho signup")
    }

    pub fn fetch_tenant(
        &self,
        endpoints: &OnboardingEndpoints,
        id_token: &str,
    ) -> Result<TenantInfo> {
        let response = self
            .http
            .get(&endpoints.tenant)
            .bearer_auth(id_token)
            .send()
            .context("failed to fetch Kumiho tenant")?;
        decode_response(response, "Kumiho tenant lookup")
    }

    pub fn create_service_token(
        &self,
        control_plane_url: &str,
        id_token: &str,
        token_name: &str,
    ) -> Result<ServiceTokenResult> {
        let url = service_token_url(control_plane_url);
        let response = self
            .http
            .post(&url)
            .bearer_auth(id_token)
            .json(&json!({
                "name": token_name,
                "description": "Generated by revka onboard"
            }))
            .send()
            .with_context(|| format!("failed to create Kumiho service token at {url}"))?;
        decode_response(response, "Kumiho service token")
    }

    fn firebase_password_request(
        &self,
        action: &str,
        firebase: &FirebaseConfig,
        email: &str,
        password: &str,
    ) -> Result<FirebaseSession> {
        let url = firebase_auth_url(action, &firebase.api_key);
        let response = self
            .http
            .post(url)
            .json(&json!({
                "email": email,
                "password": password,
                "returnSecureToken": true
            }))
            .send()
            .with_context(|| format!("failed to call Firebase {action}"))?;
        decode_response(response, "Firebase password auth")
    }
}

pub fn normalize_base_url(raw_url: &str) -> String {
    let trimmed = raw_url.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        DEFAULT_KUMIHO_WEB_URL.to_string()
    } else {
        trimmed.to_string()
    }
}

pub fn service_token_url(control_plane_url: &str) -> String {
    let normalized = normalize_control_plane_url(control_plane_url);
    format!("{normalized}/api/control-plane/service-token")
}

fn normalize_control_plane_url(raw_url: &str) -> String {
    let trimmed = raw_url.trim().trim_end_matches('/');
    trimmed
        .strip_suffix("/api/control-plane")
        .unwrap_or(trimmed)
        .to_string()
}

fn firebase_auth_url(action: &str, api_key: &str) -> String {
    format!(
        "https://identitytoolkit.googleapis.com/v1/{action}?key={}",
        urlencoding::encode(api_key)
    )
}

fn decode_response<T>(response: reqwest::blocking::Response, context: &str) -> Result<T>
where
    T: for<'de> Deserialize<'de>,
{
    let status = response.status();
    let text = response
        .text()
        .with_context(|| format!("failed to read {context} response"))?;

    if !status.is_success() {
        let message =
            extract_error_message(&text).unwrap_or_else(|| text.chars().take(500).collect());
        bail!("{context} failed: HTTP {status}: {message}");
    }

    serde_json::from_str(&text).with_context(|| format!("failed to parse {context} response"))
}

fn extract_error_message(text: &str) -> Option<String> {
    let value: Value = serde_json::from_str(text).ok()?;
    value
        .get("error")
        .and_then(|error| {
            error
                .get("message")
                .and_then(Value::as_str)
                .or_else(|| error.as_str())
        })
        .or_else(|| value.get("message").and_then(Value::as_str))
        .map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_base_url_strips_trailing_slashes() {
        assert_eq!(
            normalize_base_url(" https://kumiho.example/// "),
            "https://kumiho.example"
        );
        assert_eq!(normalize_base_url(""), DEFAULT_KUMIHO_WEB_URL);
    }

    #[test]
    fn service_token_url_accepts_base_or_nested_control_plane_url() {
        assert_eq!(
            service_token_url("https://control.kumiho.cloud"),
            "https://control.kumiho.cloud/api/control-plane/service-token"
        );
        assert_eq!(
            service_token_url("https://control.kumiho.cloud/api/control-plane/"),
            "https://control.kumiho.cloud/api/control-plane/service-token"
        );
    }

    #[test]
    fn extract_error_message_reads_firebase_error_shape() {
        assert_eq!(
            extract_error_message(r#"{"error":{"message":"EMAIL_EXISTS"}}"#).as_deref(),
            Some("EMAIL_EXISTS")
        );
        assert_eq!(
            extract_error_message(r#"{"error":"payment_required"}"#).as_deref(),
            Some("payment_required")
        );
    }

    #[test]
    fn firebase_auth_url_escapes_api_key() {
        assert_eq!(
            firebase_auth_url("accounts:signUp", "abc+123"),
            "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=abc%2B123"
        );
    }
}
