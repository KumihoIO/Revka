"""HTTP client to the TypeScript Session Manager sidecar.

Communicates over Unix socket. Falls back gracefully if sidecar is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

from ._log import _log
from .circuit_breaker import CircuitBreaker
from .failure_classification import circuit_breaker_open, sidecar_unavailable

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

# Per-operation timeout overrides (seconds)
_TIMEOUT_STATUS = 5      # get_agent, list_agents, health
_TIMEOUT_MUTATE = 15     # create_agent, send_query, interrupt, close
_TIMEOUT_DEFAULT = 30    # fallback

SOCKET_PATH = os.path.expanduser("~/.construct/operator_mcp/session-manager.sock")
SIDECAR_DIR = os.path.expanduser("~/.construct/operator_mcp/session-manager")


class SessionManagerClient:
    """HTTP client to the TS session manager sidecar (Unix socket).

    Reuses a single persistent httpx.AsyncClient to avoid FD churn
    during tight polling loops (e.g. wait_for_agent).
    """

    def __init__(self, socket_path: str = SOCKET_PATH) -> None:
        self.socket_path = socket_path
        self._process: asyncio.subprocess.Process | None = None
        self._available = False
        self._persistent: httpx.AsyncClient | None = None
        self.breaker = CircuitBreaker("sidecar", failure_threshold=3, recovery_timeout=10.0)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def _transport(self) -> httpx.AsyncHTTPTransport:
        return httpx.AsyncHTTPTransport(uds=self.socket_path)

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a persistent httpx client, creating one if needed."""
        if self._persistent is None or self._persistent.is_closed:
            self._persistent = httpx.AsyncClient(
                transport=self._transport(),
                base_url="http://construct-session-manager",
                timeout=30,
            )
        return self._persistent

    async def _close_client(self) -> None:
        """Close the persistent client (e.g. after socket reset)."""
        if self._persistent and not self._persistent.is_closed:
            try:
                await self._persistent.aclose()
            except Exception:
                pass
        self._persistent = None

    # -- Lifecycle ------------------------------------------------------------

    async def start(self) -> bool:
        """Start the session manager sidecar if not running.

        Uses a file lock to prevent multiple operator processes from
        spawning duplicate session managers simultaneously.
        """
        if self.is_running:
            return True

        # Check if an existing session-manager (from a sibling operator
        # process) is already listening on the socket.  Spawning a new one
        # would delete the socket and orphan agents tracked by the old one.
        if os.path.exists(self.socket_path):
            try:
                health = await self.health()
                if health:
                    _log("Session manager sidecar already running (existing socket)")
                    return True
            except Exception:
                pass  # Socket exists but not responding — will be replaced

        dist_entry = os.path.join(SIDECAR_DIR, "dist", "index.js")
        if not os.path.exists(dist_entry):
            _log(f"Session manager not built: {dist_entry} not found")
            return False

        # Acquire spawn lock to prevent racing sibling operator processes
        from . import _fcntl_compat as fcntl
        lock_path = self.socket_path + ".lock"
        try:
            lock_fd = open(lock_path, "w")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            _log("Session manager spawn lock held — another process is starting it")
            # Wait for the other process to finish starting
            for _ in range(50):
                await asyncio.sleep(0.1)
                if os.path.exists(self.socket_path):
                    try:
                        health = await self.health()
                        if health:
                            _log("Session manager started by sibling process")
                            return True
                    except Exception:
                        pass
            _log("Session manager not available after waiting for sibling")
            return False

        _log(f"Starting session manager sidecar: {dist_entry}")
        try:
            # Redirect stdout/stderr to log files instead of PIPE.
            # PIPE buffers are only 64 KB on macOS — if nobody drains them
            # the Node process deadlocks once the buffer fills (e.g. when
            # the Claude Agent SDK streams stderr from Claude Code).
            log_dir = os.path.expanduser("~/.construct/logs")
            os.makedirs(log_dir, exist_ok=True)
            self._sm_stdout = open(os.path.join(log_dir, "session-manager.stdout.log"), "a")
            self._sm_stderr = open(os.path.join(log_dir, "session-manager.stderr.log"), "a")
            # Build environment for the session manager and its child
            # Claude Code processes.  Claude Code stores OAuth credentials
            # in the macOS Keychain keyed by __CFBundleIdentifier — without
            # this the child claude processes cannot authenticate.
            sm_env = os.environ.copy()
            sm_env.setdefault(
                "__CFBundleIdentifier", "com.anthropic.claudefordesktop"
            )
            self._process = await asyncio.create_subprocess_exec(
                "node", dist_entry,
                "--socket", self.socket_path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=self._sm_stdout,
                stderr=self._sm_stderr,
                env=sm_env,
            )
            # Wait for socket to appear AND health check to pass.
            # Just checking file existence is not enough — the socket file
            # can exist before the server is ready to accept connections.
            for i in range(50):  # 5 seconds
                await asyncio.sleep(0.1)
                if os.path.exists(self.socket_path):
                    # Socket file exists — now verify it's healthy
                    try:
                        health = await self.health()
                        if health:
                            self._available = True
                            _log(f"Session manager sidecar started and healthy "
                                 f"(agents={health.get('agents', 0)})")
                            try:
                                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                                lock_fd.close()
                            except Exception:
                                pass
                            return True
                    except Exception:
                        pass  # Socket exists but not ready yet — keep waiting
            _log("Session manager sidecar failed to create socket")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
            return False
        except Exception as e:
            _log(f"Failed to start session manager: {e}")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
            return False

    async def health(self) -> dict[str, Any] | None:
        """Check sidecar health. Also serves as circuit breaker probe."""
        try:
            client = await self._get_client()
            resp = await client.get("/health", timeout=_TIMEOUT_STATUS)
            if resp.status_code == 200:
                self._available = True
                await self.breaker.record_success()
                return resp.json()
        except Exception:
            self._available = False
            await self.breaker.record_failure()
            await self._close_client()
        return None

    async def ensure_running(self) -> bool:
        """Ensure the sidecar is up. Start it if needed.

        Always probes the socket if it exists, even on a fresh operator
        process where ``_available`` is False.  This prevents spawning a
        *new* session-manager when one from a sibling operator MCP is
        already running — which would delete the socket and orphan agents
        tracked by the old instance.
        """
        if self._available or os.path.exists(self.socket_path):
            health = await self.health()
            if health:
                return True
        return await self.start()

    async def stop(self) -> None:
        """Stop the sidecar."""
        await self._close_client()
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        self._process = None
        self._available = False
        for fh in (getattr(self, "_sm_stdout", None), getattr(self, "_sm_stderr", None)):
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass

    # -- Agent API ------------------------------------------------------------

    async def create_agent(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new agent session via the sidecar.

        Retries once on transient HTTP errors (stale socket, brief overload).
        """
        if not await self.breaker.allow_request():
            return circuit_breaker_open(self.breaker.status())
        if not await self.ensure_running():
            return sidecar_unavailable()

        last_err = ""
        for attempt in range(2):
            try:
                client = await self._get_client()
                resp = await client.post("/agents", json=config, timeout=_TIMEOUT_MUTATE)
                result = resp.json()
                if resp.status_code < 400:
                    await self.breaker.record_success()
                return result
            except Exception as e:
                last_err = str(e)
                _log(f"create_agent via sidecar failed (attempt {attempt + 1}/2): {e}")
                if attempt == 0:
                    # Stale socket — close client and retry after brief backoff
                    await self._close_client()
                    await asyncio.sleep(1.0)
                else:
                    await self.breaker.record_failure()

        return {"error": last_err}

    async def send_query(self, agent_id: str, prompt: str) -> dict[str, Any]:
        """Send a follow-up prompt to an agent.

        Retries once on transient HTTP errors.
        """
        if not await self.breaker.allow_request():
            return circuit_breaker_open(self.breaker.status())

        last_err = ""
        for attempt in range(2):
            try:
                client = await self._get_client()
                resp = await client.post(f"/agents/{agent_id}/query", json={"prompt": prompt}, timeout=_TIMEOUT_MUTATE)
                result = resp.json()
                if resp.status_code < 400:
                    await self.breaker.record_success()
                return result
            except Exception as e:
                last_err = str(e)
                _log(f"send_query via sidecar failed (attempt {attempt + 1}/2): {e}")
                if attempt == 0:
                    await self._close_client()
                    await asyncio.sleep(1.0)
                else:
                    await self.breaker.record_failure()

        return {"error": last_err}

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get agent info."""
        if not await self.breaker.allow_request():
            return None
        try:
            client = await self._get_client()
            resp = await client.get(f"/agents/{agent_id}", timeout=_TIMEOUT_STATUS)
            if resp.status_code == 200:
                await self.breaker.record_success()
                return resp.json()
        except Exception:
            await self.breaker.record_failure()
        return None

    async def list_agents(self) -> list[dict[str, Any]]:
        """List all active agents."""
        if not await self.breaker.allow_request():
            return []
        try:
            client = await self._get_client()
            resp = await client.get("/agents", timeout=_TIMEOUT_STATUS)
            if resp.status_code == 200:
                await self.breaker.record_success()
            data = resp.json()
            return data.get("agents", [])
        except Exception:
            await self.breaker.record_failure()
            return []

    async def interrupt_agent(self, agent_id: str) -> dict[str, Any]:
        """Interrupt a running agent."""
        if not await self.breaker.allow_request():
            return circuit_breaker_open(self.breaker.status())
        try:
            client = await self._get_client()
            resp = await client.post(f"/agents/{agent_id}/interrupt", timeout=_TIMEOUT_MUTATE)
            if resp.status_code < 400:
                await self.breaker.record_success()
            return resp.json()
        except Exception as e:
            await self.breaker.record_failure()
            return {"error": str(e)}

    async def close_agent(self, agent_id: str) -> dict[str, Any]:
        """Close an agent session."""
        if not await self.breaker.allow_request():
            return circuit_breaker_open(self.breaker.status())
        try:
            client = await self._get_client()
            resp = await client.delete(f"/agents/{agent_id}", timeout=_TIMEOUT_MUTATE)
            if resp.status_code < 400:
                await self.breaker.record_success()
            return resp.json()
        except Exception as e:
            await self.breaker.record_failure()
            return {"error": str(e)}

    async def get_events(self, agent_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Get timeline events for an agent."""
        if not await self.breaker.allow_request():
            return []
        try:
            client = await self._get_client()
            resp = await client.get(f"/agents/{agent_id}/events", params={"since": since}, timeout=_TIMEOUT_STATUS)
            if resp.status_code == 200:
                await self.breaker.record_success()
            data = resp.json()
            return data.get("events", [])
        except Exception:
            await self.breaker.record_failure()
            return []

    async def stream_events(self, agent_id: str) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to SSE event stream for an agent. Yields parsed event dicts.

        Uses a dedicated client for long-lived SSE connections so the
        persistent client stays available for short requests.
        """
        try:
            stream_client = httpx.AsyncClient(
                transport=self._transport(),
                base_url="http://construct-session-manager",
                timeout=None,
            )
            async with stream_client:
                async with stream_client.stream(
                    "GET",
                    f"/agents/{agent_id}/stream",
                ) as resp:
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            raw, buffer = buffer.split("\n\n", 1)
                            for line in raw.split("\n"):
                                if line.startswith("data: "):
                                    try:
                                        yield json.loads(line[6:])
                                    except json.JSONDecodeError:
                                        pass
        except Exception as e:
            _log(f"SSE stream error for {agent_id}: {e}")

    async def stream_all_events(self) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to the global SSE event stream (all agents).

        Uses a dedicated client for the long-lived SSE connection.
        """
        try:
            stream_client = httpx.AsyncClient(
                transport=self._transport(),
                base_url="http://construct-session-manager",
                timeout=None,
            )
            async with stream_client:
                async with stream_client.stream(
                    "GET",
                    "/stream",
                ) as resp:
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            raw, buffer = buffer.split("\n\n", 1)
                            for line in raw.split("\n"):
                                if line.startswith("data: "):
                                    try:
                                        yield json.loads(line[6:])
                                    except json.JSONDecodeError:
                                        pass
        except Exception as e:
            _log(f"Global SSE stream error: {e}")

    # -- Chat API --------------------------------------------------------------

    async def chat_create_room(self, name: str, purpose: str = "") -> dict[str, Any]:
        """Create a chat room."""
        try:
            client = await self._get_client()
            resp = await client.post("/chat/rooms", json={"name": name, "purpose": purpose})
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def chat_list_rooms(self) -> list[dict[str, Any]]:
        """List all chat rooms."""
        try:
            client = await self._get_client()
            resp = await client.get("/chat/rooms")
            data = resp.json()
            return data.get("rooms", [])
        except Exception:
            return []

    async def chat_post_message(
        self,
        room_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        mentions: list[str] | None = None,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        """Post a message to a chat room."""
        try:
            body: dict[str, Any] = {
                "senderId": sender_id,
                "senderName": sender_name,
                "content": content,
            }
            if mentions:
                body["mentions"] = mentions
            if reply_to:
                body["replyTo"] = reply_to
            client = await self._get_client()
            resp = await client.post(f"/chat/rooms/{room_id}/messages", json=body)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def chat_read_messages(
        self, room_id: str, limit: int = 50, since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read messages from a chat room."""
        try:
            params: dict[str, Any] = {"limit": limit}
            if since:
                params["since"] = since
            client = await self._get_client()
            resp = await client.get(f"/chat/rooms/{room_id}/messages", params=params)
            data = resp.json()
            return data.get("messages", [])
        except Exception:
            return []

    async def chat_wait_message(self, room_id: str, timeout: int = 30000) -> dict[str, Any] | None:
        """Wait for a new message in a chat room (long-poll)."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"/chat/rooms/{room_id}/wait",
                params={"timeout": timeout},
                timeout=timeout / 1000 + 5,  # HTTP timeout slightly longer
            )
            if resp.status_code == 204:
                return None
            return resp.json()
        except Exception:
            return None

    async def chat_delete_room(self, room_id: str) -> dict[str, Any]:
        """Delete a chat room."""
        try:
            client = await self._get_client()
            resp = await client.delete(f"/chat/rooms/{room_id}")
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    # -- Permission API --------------------------------------------------------

    async def list_pending_permissions(self) -> list[dict[str, Any]]:
        """List all pending permission requests across agents."""
        try:
            client = await self._get_client()
            resp = await client.get("/permissions")
            data = resp.json()
            return data.get("pending", [])
        except Exception:
            return []

    async def respond_to_permission(
        self, request_id: str, action: str, by: str = "operator",
    ) -> dict[str, Any]:
        """Approve or deny a pending permission request."""
        try:
            client = await self._get_client()
            resp = await client.post(
                f"/permissions/{request_id}/respond",
                json={"action": action, "by": by},
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def get_permission_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent permission request history."""
        try:
            client = await self._get_client()
            resp = await client.get("/permissions/history", params={"limit": limit})
            data = resp.json()
            return data.get("history", [])
        except Exception:
            return []
