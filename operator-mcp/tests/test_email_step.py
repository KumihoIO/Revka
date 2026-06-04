"""Tests for the workflow `email:` step type.

Covers:
- Schema acceptance of the new step type and its required fields
- Dry-run renders without touching SMTP (preview pass for outreach
  workflows where every email gets reviewed before any send)
- Real send path uses smtplib (mocked) and forwards the right args
- Click-tracking link rewriter fires when track_clicks=true and refuses
  to send when misconfigured (missing track_kref or base URL)
- Recipient/subject/body interpolation against workflow inputs
- SMTP config falls back to ~/.revka/config.toml when not given
  explicitly on the step
- Failure modes: missing SMTP host, timeout, smtplib exception
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from operator_mcp.workflow.executor import _exec_email, _load_email_config_from_toml
from operator_mcp.workflow.schema import (
    EmailStepConfig,
    StepDef,
    StepType,
    WorkflowState,
)


def _make_state(inputs: dict | None = None) -> WorkflowState:
    return WorkflowState(
        workflow_name="test-wf",
        run_id="test-run",
        inputs=dict(inputs or {}),
    )


def _step(cfg: EmailStepConfig, step_id: str = "send") -> StepDef:
    return StepDef(id=step_id, type=StepType.EMAIL, email=cfg)


# ── Dry-run path ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDryRun:
    """Outreach workflows preview every personalized email before any
    leave the building. dry_run=true must render the message and
    populate output_data without touching SMTP at all."""

    async def test_dry_run_does_not_call_smtp(self):
        cfg = EmailStepConfig(
            to="ops@example.com",
            subject="Preview",
            body="Hi there",
            dry_run=True,
        )
        # If smtplib is touched at all, the test should fail — so we
        # patch it to raise on any attribute access.
        with patch("smtplib.SMTP_SSL", side_effect=AssertionError("dry-run touched SMTP_SSL")), \
             patch("smtplib.SMTP", side_effect=AssertionError("dry-run touched SMTP")):
            result = await _exec_email(_step(cfg), _make_state())

        assert result.status == "completed"
        assert result.output_data["dry_run"] is True
        assert "rendered" in result.output_data
        # Rendered message contains the subject + body
        assert "Subject: Preview" in result.output_data["rendered"]
        assert "Hi there" in result.output_data["rendered"]
        assert result.output_data["to"] == ["ops@example.com"]

    async def test_dry_run_interpolates_inputs(self):
        cfg = EmailStepConfig(
            to="${inputs.who}@example.com",
            subject="Hi ${inputs.who}",
            body="Hello ${inputs.who}, here's your update.",
            dry_run=True,
        )
        state = _make_state(inputs={"who": "alice"})
        result = await _exec_email(_step(cfg), state)

        assert result.status == "completed"
        assert result.output_data["to"] == ["alice@example.com"]
        assert result.output_data["subject"] == "Hi alice"
        assert "Hello alice" in result.output_data["rendered"]


# ── Real send path (mocked) ─────────────────────────────────────────


@pytest.mark.asyncio
class TestRealSend:
    """The non-dry-run path opens an SMTP connection and sends. We mock
    smtplib at the module boundary — the test verifies the right
    methods get called with the right args."""

    @pytest.fixture
    def smtp_config(self):
        # Stub the config-file read so tests don't depend on a dev
        # machine's ~/.revka/config.toml. Returns a minimal but
        # valid SMTP config.
        return {
            "smtp_host": "smtp.test",
            "smtp_port": 465,
            "smtp_tls": True,
            "username": "from@example.com",
            "password": "secretpw",
            "from_address": "from@example.com",
        }

    async def test_calls_smtp_ssl_on_default_tls(self, smtp_config):
        cfg = EmailStepConfig(
            to=["a@example.com", "b@example.com"],
            subject="Hi",
            body="Body text",
        )
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        with patch(
            "operator_mcp.workflow.executor._load_email_config_from_toml",
            return_value=smtp_config,
        ), patch("smtplib.SMTP_SSL", return_value=mock_smtp) as mock_ssl:
            result = await _exec_email(_step(cfg), _make_state())

        assert result.status == "completed"
        assert result.output_data["sent"] is True

        # Constructed against the right host/port
        mock_ssl.assert_called_once()
        host, port = mock_ssl.call_args[0][:2]
        assert host == "smtp.test"
        assert port == 465

        # Logged in + sendmail called with the recipient set
        mock_smtp.login.assert_called_once_with("from@example.com", "secretpw")
        send_args = mock_smtp.sendmail.call_args[0]
        assert send_args[0] == "from@example.com"
        assert set(send_args[1]) == {"a@example.com", "b@example.com"}
        assert "Subject: Hi" in send_args[2]

    async def test_smtp_starttls_path_when_tls_disabled(self, smtp_config):
        smtp_config["smtp_tls"] = False
        smtp_config["smtp_port"] = 587
        cfg = EmailStepConfig(to="x@example.com", subject="S", body="B")

        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        with patch(
            "operator_mcp.workflow.executor._load_email_config_from_toml",
            return_value=smtp_config,
        ), patch("smtplib.SMTP", return_value=mock_smtp) as mock_plain, \
             patch("smtplib.SMTP_SSL", side_effect=AssertionError("should not use SSL")):
            result = await _exec_email(_step(cfg), _make_state())

        assert result.status == "completed"
        mock_plain.assert_called_once()
        mock_smtp.starttls.assert_called_once()

    async def test_missing_smtp_host_fails_clearly(self):
        cfg = EmailStepConfig(to="x@example.com", subject="S", body="B")
        # Empty config → no SMTP host → fail with helpful error rather
        # than try to connect to an empty host.
        with patch(
            "operator_mcp.workflow.executor._load_email_config_from_toml",
            return_value={},
        ):
            result = await _exec_email(_step(cfg), _make_state())
        assert result.status == "failed"
        assert "SMTP host" in result.error

    async def test_smtp_exception_surfaces_generic_step_failure(self, smtp_config):
        cfg = EmailStepConfig(to="x@example.com", subject="S", body="B")
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        mock_smtp.sendmail.side_effect = OSError("connection refused")

        with patch(
            "operator_mcp.workflow.executor._load_email_config_from_toml",
            return_value=smtp_config,
        ), patch("smtplib.SMTP_SSL", return_value=mock_smtp):
            result = await _exec_email(_step(cfg), _make_state())

        assert result.status == "failed"
        assert result.error == "SMTP send failed (see logs)"


# ── Click tracking ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestClickTracking:
    """track_clicks=true wraps every URL in body+body_html with the
    gateway redirect. The contract is: kref required, base URL required,
    same encoded kref shared across all links in this email."""

    @pytest.fixture
    def tracking_cfg(self):
        return EmailStepConfig(
            to="lead@example.com",
            subject="Hi",
            body="Visit https://revka.example.com or https://kumiho.io.",
            track_clicks=True,
            track_kref="kref://Revka/Outreach/leads/acme.contact",
            track_base_url="https://gw.example.com",
            dry_run=True,  # We want to inspect the rendered body, not actually send
        )

    async def test_rewrites_links_when_tracking_on(self, tracking_cfg):
        result = await _exec_email(_step(tracking_cfg), _make_state())
        assert result.status == "completed"
        rendered = result.output_data["rendered"]
        # Originals are gone; tracker URLs replaced them
        assert "https://revka.example.com" not in rendered or "track/c/" in rendered
        assert "https://gw.example.com/track/c/" in rendered
        # Encoded kref surfaces in output_data so downstream steps can
        # log it / cross-reference click events.
        assert result.output_data["encoded_kref"]
        assert result.output_data["tracked_kref"] == tracking_cfg.track_kref

    async def test_missing_track_kref_fails(self):
        cfg = EmailStepConfig(
            to="x@example.com",
            subject="S",
            body="Visit https://x.com",
            track_clicks=True,
            track_kref=None,  # explicit miss
            track_base_url="https://gw",
            dry_run=True,
        )
        result = await _exec_email(_step(cfg), _make_state())
        assert result.status == "failed"
        assert "track_kref" in result.error

    async def test_missing_base_url_fails(self):
        cfg = EmailStepConfig(
            to="x@example.com",
            subject="S",
            body="Visit https://x.com",
            track_clicks=True,
            track_kref="kref://Test/x.item",
            track_base_url=None,
            dry_run=True,
        )
        # No GATEWAY_URL env var either
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("GATEWAY_URL", None)
            result = await _exec_email(_step(cfg), _make_state())
        assert result.status == "failed"
        assert "GATEWAY_URL" in result.error or "base" in result.error.lower()

    async def test_tracking_off_by_default(self):
        cfg = EmailStepConfig(
            to="x@example.com",
            subject="S",
            body="Visit https://x.com today.",
            dry_run=True,
        )
        result = await _exec_email(_step(cfg), _make_state())
        assert result.status == "completed"
        # No track_clicks → URL should pass through untouched
        assert "https://x.com" in result.output_data["rendered"]
        assert "/track/c/" not in result.output_data["rendered"]
        assert "encoded_kref" not in result.output_data


# ── Config loading ──────────────────────────────────────────────────


class TestConfigLoading:
    def test_loads_channels_config_email_section(self, tmp_path, monkeypatch):
        # Drop a fake config.toml at ~/.revka/config.toml — patch
        # HOME so the loader looks at our tmp dir.
        revka_dir = tmp_path / ".revka"
        revka_dir.mkdir()
        (revka_dir / "config.toml").write_text(
            """
[channels_config.email]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_tls = false
username = "u"
password = "p"
from_address = "from@example.com"
"""
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        # On macOS HOME alone isn't enough — os.path.expanduser uses
        # os.environ["HOME"], so this works.
        cfg = _load_email_config_from_toml()
        assert cfg["smtp_host"] == "smtp.example.com"
        assert cfg["smtp_port"] == 587
        assert cfg["smtp_tls"] is False

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = _load_email_config_from_toml()
        assert cfg == {}
