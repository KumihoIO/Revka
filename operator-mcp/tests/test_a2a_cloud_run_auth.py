import pytest

from operator_mcp.a2a import a2a_client


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeHttpClient:
    is_closed = False

    def __init__(self):
        self.get_calls = []
        self.post_calls = []

    async def get(self, url, headers=None):
        self.get_calls.append({"url": url, "headers": headers or {}})
        return _FakeResponse(
            payload={
                "name": "Private Cloud Run A2A",
                "url": "https://agent.example.run.app",
                "skills": [],
            }
        )

    async def post(self, url, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers or {}})
        return _FakeResponse(
            payload={
                "jsonrpc": "2.0",
                "result": {"id": "task-1", "status": {"state": "completed"}},
            }
        )


def test_auth_headers_keep_cloud_run_and_a2a_tokens_separate():
    headers = a2a_client._auth_headers(
        auth_token="app-token",
        cloud_run_identity_token="identity-token",
        content_type="application/json",
    )

    assert headers == {
        "Content-Type": "application/json",
        "X-Serverless-Authorization": "Bearer identity-token",
        "Authorization": "Bearer app-token",
    }


@pytest.mark.asyncio
async def test_discover_sends_cloud_run_identity_header():
    fake_http = _FakeHttpClient()
    client = a2a_client.A2AClient(timeout=1)
    client._client = fake_http

    card = await client.discover(
        "https://agent.example.run.app",
        cloud_run_identity_token="identity-token",
    )

    assert card["name"] == "Private Cloud Run A2A"
    assert fake_http.get_calls[0]["url"] == "https://agent.example.run.app/.well-known/agent-card.json"
    assert fake_http.get_calls[0]["headers"] == {
        "X-Serverless-Authorization": "Bearer identity-token",
    }


@pytest.mark.asyncio
async def test_send_task_sends_cloud_run_and_a2a_auth_headers():
    fake_http = _FakeHttpClient()
    client = a2a_client.A2AClient(timeout=1)
    client._client = fake_http

    task = await client.send_task(
        "https://agent.example.run.app/",
        message="triage incident",
        auth_token="app-token",
        cloud_run_identity_token="identity-token",
    )

    assert task["status"]["state"] == "completed"
    assert fake_http.post_calls[0]["headers"] == {
        "Content-Type": "application/json",
        "X-Serverless-Authorization": "Bearer identity-token",
        "Authorization": "Bearer app-token",
    }


@pytest.mark.asyncio
async def test_tool_discover_can_mint_cloud_run_token_with_gcloud(monkeypatch):
    captured = {}

    async def fake_gcloud_identity_token(audience, *, timeout=20.0):
        captured["audience"] = audience
        captured["timeout"] = timeout
        return "minted-token"

    class FakeA2AClient:
        async def discover(self, url, *, auth_token=None, cloud_run_identity_token=None):
            captured["url"] = url
            captured["auth_token"] = auth_token
            captured["cloud_run_identity_token"] = cloud_run_identity_token
            return {"name": "Private Cloud Run A2A", "skills": [], "capabilities": {}}

    monkeypatch.setattr(a2a_client, "_gcloud_identity_token", fake_gcloud_identity_token)
    monkeypatch.setattr(a2a_client, "get_client", lambda timeout=30.0: FakeA2AClient())

    result = await a2a_client.tool_a2a_discover(
        {
            "url": "https://agent.example.run.app",
            "cloud_run_auth": "gcloud",
            "auth_token": "app-token",
            "timeout": 3,
        }
    )

    assert result["discovered"] is True
    assert captured == {
        "audience": "https://agent.example.run.app",
        "timeout": 20.0,
        "url": "https://agent.example.run.app",
        "auth_token": "app-token",
        "cloud_run_identity_token": "minted-token",
    }
