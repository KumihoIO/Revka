#!/usr/bin/env python3
"""Local HTTP bridge from Revka to the Kumiho Python SDK.

The bridge intentionally exposes a small FastAPI-compatible subset under
``/api/v1`` so the Rust gateway can bypass the hosted BFF for high-frequency
dashboard calls while preserving the existing JSON shapes and HTTP fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

os.environ.pop("KUMIHO_AUTO_CONFIGURE", None)
if (
    not os.environ.get("KUMIHO_CONTROL_PLANE_URL")
    and os.environ.get("KUMIHO_CONTROL_PLANE_API_URL")
):
    os.environ["KUMIHO_CONTROL_PLANE_URL"] = os.environ["KUMIHO_CONTROL_PLANE_API_URL"]

try:
    import grpc
    import kumiho
    from kumiho import Kref
except Exception as exc:  # pragma: no cover - exercised by Rust fallback
    grpc = None  # type: ignore[assignment]
    kumiho = None  # type: ignore[assignment]
    Kref = None  # type: ignore[assignment]
    IMPORT_ERROR: BaseException | None = exc
else:
    IMPORT_ERROR = None


_CLIENTS: dict[str, Any] = {}
_CLIENT_LOCK = threading.RLock()


class LoopbackThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind() calls socket.getfqdn(host), which can block on
        # reverse DNS for loopback addresses on some hosts. The bridge is
        # loopback-only and does not need that canonical name.
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    if not values:
        return default
    return values[0]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _routing_env_key() -> str:
    parts = []
    for name in (
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_DISABLE_AUTO_DISCOVERY",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ADDRESS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SSL_TARGET_OVERRIDE",
    ):
        parts.append(f"{name}={os.environ.get(name, '').strip()}")
    return "\0".join(parts)


def _cached_discovery_route(tenant_hint: str) -> tuple[str, str] | None:
    try:
        from kumiho.discovery import DiscoveryCache

        cache_key = tenant_hint or "__default__"
        record = DiscoveryCache().load(cache_key)
        if record is None:
            return None
        target = record.region.grpc_authority or record.region.server_url
        tenant_id = getattr(record, "tenant_id", "")
        if not target or not tenant_id:
            return None
        return target, tenant_id
    except Exception:
        return None


def _client_for(token: str) -> Any:
    if kumiho is None:
        raise RuntimeError(f"kumiho SDK unavailable: {IMPORT_ERROR}")
    token = token.strip()
    if not token:
        # Tokenless local Community Edition. The Rust gateway routes CE traffic
        # here because CE serves gRPC (not JSON REST), so the hosted FastAPI
        # fallback cannot decode it. The kumiho SDK reads
        # KUMIHO_LOCAL_SERVER_ENDPOINT, probes http://<endpoint>/api/_live, sees
        # deployment_mode=self_hosted_ce, and connects tokenless over gRPC.
        ce_endpoint = os.environ.get("KUMIHO_LOCAL_SERVER_ENDPOINT", "").strip()
        if not ce_endpoint:
            raise PermissionError("missing Kumiho token")
        key = f"local-ce:{ce_endpoint}"
        with _CLIENT_LOCK:
            client = _CLIENTS.get(key)
            if client is None:
                client = kumiho.connect(enable_auto_login=False)
                _CLIENTS[key] = client
            return client
    tenant_hint = os.environ.get("KUMIHO_TENANT_HINT", "").strip()
    key = f"{_token_hash(token)}:{tenant_hint}:{_routing_env_key()}"
    with _CLIENT_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            cached_route = _cached_discovery_route(tenant_hint)
            if cached_route:
                target, tenant_id = cached_route
                client = kumiho.connect(
                    endpoint=target,
                    token=token,
                    enable_auto_login=False,
                    use_discovery=False,
                    default_metadata=[("x-tenant-id", tenant_id)],
                )
            else:
                client = kumiho.connect(
                    token=token,
                    enable_auto_login=False,
                    use_discovery=True,
                    tenant_hint=tenant_hint or None,
                )
            _CLIENTS[key] = client
        return client


def _kref(value: Any) -> str:
    if value is None:
        return ""
    return getattr(value, "uri", str(value))


def _metadata(value: Any) -> dict[str, str]:
    raw = getattr(value, "metadata", {}) or {}
    return {str(k): str(v) for k, v in dict(raw).items()}


def _item(item: Any) -> dict[str, Any]:
    return {
        "kref": _kref(item.kref),
        "name": getattr(item, "name", ""),
        "item_name": getattr(item, "item_name", ""),
        "kind": getattr(item, "kind", ""),
        "deprecated": bool(getattr(item, "deprecated", False)),
        "created_at": getattr(item, "created_at", None),
        "author": getattr(item, "author", None),
        "username": getattr(item, "username", None),
        "author_display": getattr(item, "username", None) or getattr(item, "author", None),
        "metadata": _metadata(item),
    }


def _revision(revision: Any) -> dict[str, Any]:
    return {
        "kref": _kref(revision.kref),
        "item_kref": _kref(revision.item_kref),
        "number": int(getattr(revision, "number", 0)),
        "latest": bool(getattr(revision, "latest", False)),
        "tags": list(getattr(revision, "tags", []) or []),
        "metadata": _metadata(revision),
        "deprecated": bool(getattr(revision, "deprecated", False)),
        "created_at": getattr(revision, "created_at", None),
        "author": getattr(revision, "author", None),
        "username": getattr(revision, "username", None),
        "author_display": getattr(revision, "username", None) or getattr(revision, "author", None),
    }


def _artifact(artifact: Any) -> dict[str, Any]:
    return {
        "kref": _kref(artifact.kref),
        "name": getattr(artifact, "name", ""),
        "location": getattr(artifact, "location", ""),
        "revision_kref": _kref(artifact.revision_kref),
        "item_kref": _kref(getattr(artifact, "item_kref", None)) or None,
        "deprecated": bool(getattr(artifact, "deprecated", False)),
        "created_at": getattr(artifact, "created_at", None),
        "author": getattr(artifact, "author", None),
        "username": getattr(artifact, "username", None),
        "author_display": getattr(artifact, "username", None) or getattr(artifact, "author", None),
        "metadata": _metadata(artifact),
    }


def _space(space: Any) -> dict[str, Any]:
    path = getattr(space, "path", "")
    parent = None
    if path and path != "/":
        stripped = path.rstrip("/")
        idx = stripped.rfind("/")
        if idx > 0:
            parent = stripped[:idx]
        elif idx == 0:
            parent = "/"
    return {
        "path": path,
        "name": getattr(space, "name", ""),
        "parent_path": parent,
        "created_at": getattr(space, "created_at", None),
        "author": getattr(space, "author", None),
        "username": getattr(space, "username", None),
        "author_display": getattr(space, "username", None) or getattr(space, "author", None),
    }


def _project(project: Any) -> dict[str, Any]:
    return {
        "project_id": getattr(project, "project_id", ""),
        "name": getattr(project, "name", ""),
        "description": getattr(project, "description", ""),
        "created_at": getattr(project, "created_at", None),
        "updated_at": getattr(project, "updated_at", None),
        "deprecated": bool(getattr(project, "deprecated", False)),
        "allow_public": bool(getattr(project, "allow_public", False)),
    }


def _edge(edge: Any) -> dict[str, Any]:
    return {
        "source_kref": _kref(getattr(edge, "source_kref", None)),
        "target_kref": _kref(getattr(edge, "target_kref", None)),
        "edge_type": getattr(edge, "edge_type", ""),
        "created_at": getattr(edge, "created_at", None),
        "metadata": _metadata(edge),
    }


def _bundle_member(member: Any) -> dict[str, Any]:
    return {
        "item_kref": _kref(getattr(member, "item_kref", None)),
        "added_at": getattr(member, "added_at", None),
        "added_by": getattr(member, "added_by", None),
        "added_in_revision": getattr(member, "added_in_revision", None),
    }


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


class UnsupportedRoute(Exception):
    def __init__(self, path: str, method: str) -> None:
        super().__init__(f"unsupported Kumiho SDK bridge route: {method} {path}")


def _unsupported_payload(exc: UnsupportedRoute) -> dict[str, Any]:
    return {
        "error": str(exc),
        "error_code": "kumiho_sdk_bridge_unsupported",
    }


def _grpc_status(exc: BaseException) -> int:
    if grpc is None or not isinstance(exc, grpc.RpcError):
        if isinstance(exc, UnsupportedRoute):
            return HTTPStatus.NOT_IMPLEMENTED
        if isinstance(exc, PermissionError):
            return HTTPStatus.UNAUTHORIZED
        if isinstance(exc, FileNotFoundError):
            return HTTPStatus.NOT_FOUND
        return HTTPStatus.INTERNAL_SERVER_ERROR
    code = exc.code()
    if code == grpc.StatusCode.NOT_FOUND:
        return HTTPStatus.NOT_FOUND
    if code == grpc.StatusCode.UNAUTHENTICATED:
        return HTTPStatus.UNAUTHORIZED
    if code == grpc.StatusCode.PERMISSION_DENIED:
        return HTTPStatus.FORBIDDEN
    if code == grpc.StatusCode.ALREADY_EXISTS:
        return HTTPStatus.CONFLICT
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return HTTPStatus.BAD_REQUEST
    if code == grpc.StatusCode.UNAVAILABLE:
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.INTERNAL_SERVER_ERROR


def _error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, UnsupportedRoute):
        return _unsupported_payload(exc)
    message = str(exc)
    if grpc is not None and isinstance(exc, grpc.RpcError):
        try:
            message = exc.details() or message
        except Exception:
            # Ignore details() extraction errors and keep the fallback str(exc) message.
            message = message
    return {
        "error": message,
        "error_code": "kumiho_sdk_bridge_error",
        "exception": exc.__class__.__name__,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "RevkaKumihoSdkBridge/1"

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def log_message(self, fmt: str, *args: Any) -> None:
        if _truthy(os.environ.get("KUMIHO_SDK_BRIDGE_LOG_REQUESTS")):
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("x-revka-kumiho-transport", "sdk-bridge")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/health", "/api/v1/health"}:
            if IMPORT_ERROR is None:
                self._send(HTTPStatus.OK, {"ok": True})
            else:
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": str(IMPORT_ERROR)},
                )
            return
        if not parsed.path.startswith("/api/v1/"):
            exc = UnsupportedRoute(parsed.path, method)
            self._send(HTTPStatus.NOT_IMPLEMENTED, _unsupported_payload(exc))
            return

        token = (
            self.headers.get("x-kumiho-token", "")
            or self.headers.get("authorization", "").removeprefix("Bearer ")
        )
        params = parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path.removeprefix("/api/v1")

        try:
            client = _client_for(token)
            payload = self._route(client, method, path, params)
            self._send(HTTPStatus.OK, payload)
        except Exception as exc:
            if _truthy(os.environ.get("KUMIHO_SDK_BRIDGE_DEBUG")):
                traceback.print_exc(file=sys.stderr)
            self._send(_grpc_status(exc), _error_payload(exc))

    def _route(
        self,
        client: Any,
        method: str,
        path: str,
        params: dict[str, list[str]],
    ) -> Any:
        body = _json_body(self) if method in {"POST", "PUT", "PATCH"} else {}

        if method == "GET" and path == "/projects":
            return [_project(p) for p in client.get_projects()]

        if method == "POST" and path == "/projects":
            return _project(client.create_project(body["name"], body.get("description") or ""))

        if method == "GET" and path == "/spaces":
            parent = _first(params, "parent_path", "/")
            recursive = _truthy(_first(params, "recursive", "false"))
            return [_space(s) for s in client.get_child_spaces(parent, recursive=recursive)]

        if method == "POST" and path == "/spaces":
            return _space(client.create_space(body["parent_path"], body["name"]))

        if method == "GET" and path == "/items":
            offset = int(_first(params, "offset", "0") or "0")
            if offset:
                raise UnsupportedRoute(path, method)
            space_path = _first(params, "space_path")
            name_filter = _first(params, "name_filter")
            kind_filter = _first(params, "kind_filter")
            include_deprecated = _truthy(_first(params, "include_deprecated", "false"))
            limit = int(_first(params, "limit", "100") or "100")
            return [
                _item(i)
                for i in client.get_items(
                    space_path,
                    item_name_filter=name_filter,
                    kind_filter=kind_filter,
                    page_size=limit,
                    include_deprecated=include_deprecated,
                )
            ]

        if method == "GET" and path == "/items/by-kref":
            return _item(client.get_item_by_kref(_first(params, "kref")))

        if method == "POST" and path == "/items":
            item = client.create_item(
                body["space_path"],
                body["item_name"],
                body["kind"],
                metadata=body.get("metadata") or {},
            )
            return _item(client.get_item_by_kref(_kref(item.kref)))

        if method == "DELETE" and path == "/items/by-kref":
            client.delete_item(Kref(_first(params, "kref")), _truthy(_first(params, "force", "true")))
            return {"success": True}

        if method == "POST" and path == "/items/deprecate":
            kref = _first(params, "kref")
            client.set_deprecated(Kref(kref), _truthy(_first(params, "deprecated", "true")))
            return _item(client.get_item_by_kref(kref))

        if method == "GET" and path == "/items/fulltext-search":
            return [
                {"item": _item(r.item), "score": float(getattr(r, "score", 0.0))}
                for r in client.search(
                    _first(params, "query"),
                    context_filter=_first(params, "context"),
                    kind_filter=_first(params, "kind"),
                    include_deprecated=_truthy(_first(params, "include_deprecated", "false")),
                )
            ]

        if method == "POST" and path == "/revisions":
            return _revision(client.create_revision(Kref(body["item_kref"]), body.get("metadata") or {}))

        if method == "GET" and path == "/revisions":
            return [_revision(r) for r in client.get_revisions(Kref(_first(params, "item_kref")))]

        if method == "GET" and path == "/revisions/by-kref":
            kref = _first(params, "kref")
            tag = _first(params, "t") or _first(params, "tag")
            if tag and "?" not in kref:
                kref = f"{kref}?t={tag}"
            return _revision(client.get_revision(kref))

        if method == "GET" and path == "/revisions/latest":
            rev = client.get_latest_revision(Kref(_first(params, "item_kref")))
            if rev is None:
                raise FileNotFoundError("revision not found")
            return _revision(rev)

        if method == "POST" and path == "/revisions/batch":
            revisions, not_found = client.batch_get_revisions(
                item_krefs=body.get("item_krefs") or [],
                revision_krefs=body.get("revision_krefs") or [],
                tag=body.get("tag") or "latest",
                allow_partial=bool(body.get("allow_partial", True)),
            )
            return {
                "revisions": [_revision(r) for r in revisions],
                "not_found": list(not_found),
                "requested_count": len(body.get("item_krefs") or body.get("revision_krefs") or []),
                "found_count": len(revisions),
            }

        if method == "POST" and path == "/revisions/tags":
            client.tag_revision(Kref(_first(params, "kref")), body["tag"])
            return {"success": True}

        if method == "POST" and path == "/revisions/deprecate":
            kref = _first(params, "kref")
            client.set_deprecated(Kref(kref), _truthy(_first(params, "deprecated", "true")))
            return _revision(client.get_revision(kref))

        if method == "POST" and path == "/artifacts":
            return _artifact(
                client.create_artifact(
                    Kref(body["revision_kref"]),
                    body["name"],
                    body["location"],
                    metadata=body.get("metadata") or {},
                )
            )

        if method == "GET" and path == "/artifacts":
            return [_artifact(a) for a in client.get_artifacts(Kref(_first(params, "revision_kref")))]

        if method == "GET" and path == "/artifacts/by-location":
            return [_artifact(a) for a in client.get_artifacts_by_location(_first(params, "location"))]

        if method == "GET" and path == "/artifacts/by-kref":
            artifact_kref = _first(params, "kref")
            if artifact_kref:
                return _artifact(client.get_artifact_by_kref(artifact_kref))
            return _artifact(client.get_artifact(Kref(_first(params, "revision_kref")), _first(params, "name")))

        if method == "POST" and path == "/artifacts/deprecate":
            kref = _first(params, "kref")
            client.set_deprecated(Kref(kref), _truthy(_first(params, "deprecated", "true")))
            return _artifact(client.get_artifact_by_kref(kref))

        if method == "POST" and path == "/bundles":
            return _item(
                client.create_bundle(
                    body["space_path"],
                    body["bundle_name"],
                    metadata=body.get("metadata") or {},
                )
            )

        if method == "GET" and path == "/bundles/by-kref":
            return _item(client.get_bundle_by_kref(_first(params, "kref")))

        if method == "DELETE" and path == "/bundles/by-kref":
            client.delete_item(Kref(_first(params, "kref")), _truthy(_first(params, "force", "true")))
            return {"success": True}

        if method == "POST" and path == "/bundles/members/add":
            ok, message, rev = client.add_bundle_member(
                Kref(body["bundle_kref"]),
                Kref(body["item_kref"]),
                metadata=body.get("metadata") or {},
            )
            return {"success": ok, "message": message, "new_revision": _revision(rev) if rev else None}

        if method == "POST" and path == "/bundles/members/remove":
            ok, message, rev = client.remove_bundle_member(
                Kref(body["bundle_kref"]),
                Kref(body["item_kref"]),
                metadata=body.get("metadata") or {},
            )
            return {"success": ok, "message": message, "new_revision": _revision(rev) if rev else None}

        if method == "GET" and path == "/bundles/members":
            members, _revision_number, total = client.get_bundle_members(Kref(_first(params, "bundle_kref")))
            return {"members": [_bundle_member(m) for m in members], "total_count": total}

        if method == "POST" and path == "/edges":
            source_kref = body.get("source_kref") or body["source_revision_kref"]
            target_kref = body.get("target_kref") or body["target_revision_kref"]
            edge = client.create_edge(
                client.get_revision(source_kref),
                client.get_revision(target_kref),
                body["edge_type"],
                metadata=body.get("metadata") or {},
            )
            return _edge(edge)

        if method == "GET" and path == "/edges":
            direction = int(_first(params, "direction", "0") or "0")
            return [
                _edge(edge)
                for edge in client.get_edges(
                    Kref(_first(params, "kref")),
                    edge_type_filter=_first(params, "edge_type"),
                    direction=direction,
                )
            ]

        if method == "DELETE" and path == "/edges":
            client.delete_edge(
                Kref(_first(params, "source_kref")),
                Kref(_first(params, "target_kref")),
                _first(params, "edge_type"),
            )
            return {"success": True}

        raise UnsupportedRoute(path, method)


def main() -> int:
    host = os.environ.get("KUMIHO_SDK_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("KUMIHO_SDK_BRIDGE_PORT", "0") or "0")
    server = LoopbackThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(
        f"revka kumiho sdk bridge listening on http://{host}:{server.server_port}\n"
    )
    sys.stderr.flush()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
