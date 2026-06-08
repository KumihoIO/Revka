import pytest

from operator_mcp.a2a import a2a_client
from operator_mcp.workflow import executor
from operator_mcp.workflow.schema import StepDef, WorkflowState


@pytest.mark.asyncio
async def test_workflow_a2a_step_uses_cloud_run_gcloud_auth(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_resolve_step_auth(step, auth):
        captured["auth_profile"] = auth
        return {"token": "app-token"}, None

    async def fake_gcloud_identity_token(audience, *, timeout=20.0):
        captured["audience"] = audience
        captured["gcloud_timeout"] = timeout
        return "identity-token"

    class FakeA2AClient:
        async def send_task(
            self,
            endpoint_url,
            *,
            message,
            skill_id=None,
            auth_token=None,
            cloud_run_identity_token=None,
        ):
            captured["send"] = {
                "endpoint_url": endpoint_url,
                "message": message,
                "skill_id": skill_id,
                "auth_token": auth_token,
                "cloud_run_identity_token": cloud_run_identity_token,
            }
            return {"id": "task-1", "status": {"state": "working"}}

        async def poll_until_complete(
            self,
            endpoint_url,
            task_id,
            *,
            poll_interval=5.0,
            max_polls=60,
            auth_token=None,
            cloud_run_identity_token=None,
        ):
            captured["poll"] = {
                "endpoint_url": endpoint_url,
                "task_id": task_id,
                "poll_interval": poll_interval,
                "max_polls": max_polls,
                "auth_token": auth_token,
                "cloud_run_identity_token": cloud_run_identity_token,
            }
            return {
                "id": task_id,
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"type": "text", "text": "done"}]}],
            }

    monkeypatch.setattr(executor, "_resolve_step_auth", fake_resolve_step_auth)
    monkeypatch.setattr(a2a_client, "_gcloud_identity_token", fake_gcloud_identity_token)
    monkeypatch.setattr(a2a_client, "get_client", lambda timeout=60.0: FakeA2AClient())

    step = StepDef.model_validate(
        {
            "id": "call-private-agent",
            "type": "a2a",
            "a2a": {
                "url": "${inputs.track3_a2a_url}",
                "skill_id": "${inputs.skill_id}",
                "message": "Triage ${inputs.issue}",
                "timeout": 30,
                "auth": "a2a:prod",
                "cloud_run_auth": "gcloud",
                "cloud_run_audience": "${inputs.track3_a2a_url}",
                "cloud_run_auth_timeout": 15,
            },
        }
    )
    state = WorkflowState(
        workflow_name="track3",
        run_id="run-1",
        inputs={
            "track3_a2a_url": "https://private-agent.run.app",
            "skill_id": "incident-triage",
            "issue": "sev1 latency",
        },
    )

    result = await executor._exec_a2a(step, state)

    assert result.status == "completed"
    assert result.output == "done\n"
    assert captured["auth_profile"] == "a2a:prod"
    assert captured["audience"] == "https://private-agent.run.app"
    assert captured["gcloud_timeout"] == 15
    assert captured["send"] == {
        "endpoint_url": "https://private-agent.run.app",
        "message": "Triage sev1 latency",
        "skill_id": "incident-triage",
        "auth_token": "app-token",
        "cloud_run_identity_token": "identity-token",
    }
    assert captured["poll"] == {
        "endpoint_url": "https://private-agent.run.app",
        "task_id": "task-1",
        "poll_interval": 5.0,
        "max_polls": 6,
        "auth_token": "app-token",
        "cloud_run_identity_token": "identity-token",
    }
