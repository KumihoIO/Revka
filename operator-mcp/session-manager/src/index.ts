/**
 * Revka Session Manager — HTTP server on Unix socket.
 *
 * REST API for the Python operator to manage agent SDK sessions.
 * Communicates via Unix socket to avoid port conflicts.
 *
 * Endpoints:
 *   POST   /agents              → create agent session
 *   GET    /agents              → list active sessions
 *   GET    /agents/:id          → get agent info
 *   POST   /agents/:id/query    → send follow-up prompt
 *   POST   /agents/:id/interrupt → interrupt running agent
 *   DELETE /agents/:id          → close and cleanup
 *   GET    /agents/:id/events   → get timeline events (query param: since=N)
 *   GET    /agents/:id/stream   → SSE stream of events
 *   GET    /health              → health check
 *   POST   /chat/rooms          → create chat room
 *   GET    /chat/rooms          → list chat rooms
 *   GET    /chat/rooms/:id      → get room info
 *   DELETE /chat/rooms/:id      → delete room
 *   POST   /chat/rooms/:id/messages → post message
 *   GET    /chat/rooms/:id/messages → read messages (query: limit, since)
 *   GET    /chat/rooms/:id/wait → wait for new message (query: timeout)
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { unlinkSync, existsSync, writeFileSync, readFileSync, openSync, closeSync } from "node:fs";
import { AgentManager } from "./agent-manager.js";
import { ChatService } from "./chat-service.js";
import { PermissionHandler } from "./permission-handler.js";
import type { AgentSessionConfig, AgentEvent } from "./types.js";

const log = (msg: string) => process.stderr.write(`[session-mgr] ${msg}\n`);

const permissions = new PermissionHandler();
const manager = new AgentManager(permissions);
const chat = new ChatService();

// Wire mention-based interrupts: when an agent is mentioned in chat,
// inject the message as a follow-up prompt if the agent is idle.
chat.setMentionHandler((roomId, message) => {
  for (const mentionedId of message.mentions) {
    const agentInfo = manager.getAgent(mentionedId);
    if (agentInfo && agentInfo.status === "idle") {
      const room = chat.getRoom(roomId);
      const roomName = room?.name ?? roomId;
      const notification = `[Chat notification from ${roomName}] @${message.senderName}: ${message.content}`;
      log(`Mention interrupt: notifying agent ${mentionedId} from ${message.senderName}`);
      manager.sendQuery(mentionedId, notification).catch((err) => {
        log(`Mention interrupt failed for ${mentionedId}: ${err}`);
      });
    } else {
      log(`Mention queued: agent ${mentionedId} is ${agentInfo?.status ?? "unknown"}`);
    }
  }
});

// -- Helpers -----------------------------------------------------------------

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks).toString("utf-8");
}

function json(res: ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function parseRoute(url: string): { path: string[]; query: URLSearchParams } {
  const [pathname, search] = (url ?? "/").split("?");
  const path = pathname.split("/").filter(Boolean);
  return { path, query: new URLSearchParams(search ?? "") };
}

// -- Request handler ---------------------------------------------------------

async function handleRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const method = req.method ?? "GET";
  const { path, query } = parseRoute(req.url ?? "/");

  try {
    // GET /health
    if (method === "GET" && path[0] === "health") {
      return json(res, 200, {
        status: "ok",
        agents: manager.listAgents().length,
        chatRooms: chat.listRooms().length,
        pendingPermissions: permissions.listPending().length,
        uptime: process.uptime(),
      });
    }

    // POST /agents — create agent
    if (method === "POST" && path[0] === "agents" && !path[1]) {
      const body = JSON.parse(await readBody(req)) as AgentSessionConfig;
      const info = await manager.createAgent(body);
      return json(res, 201, info);
    }

    // GET /agents — list agents
    if (method === "GET" && path[0] === "agents" && !path[1]) {
      return json(res, 200, { agents: manager.listAgents() });
    }

    // GET /agents/:id — get agent info
    if (method === "GET" && path[0] === "agents" && path[1] && !path[2]) {
      const info = manager.getAgent(path[1]);
      if (!info) return json(res, 404, { error: `Agent not found: ${path[1]}` });
      return json(res, 200, info);
    }

    // POST /agents/:id/query — send follow-up
    if (method === "POST" && path[0] === "agents" && path[1] && path[2] === "query") {
      const body = JSON.parse(await readBody(req));
      const info = await manager.sendQuery(path[1], body.prompt);
      return json(res, 200, info);
    }

    // POST /agents/:id/interrupt — interrupt agent
    if (method === "POST" && path[0] === "agents" && path[1] && path[2] === "interrupt") {
      await manager.interruptAgent(path[1]);
      const info = manager.getAgent(path[1]);
      return json(res, 200, info ?? { status: "not_found" });
    }

    // DELETE /agents/:id — close agent
    if (method === "DELETE" && path[0] === "agents" && path[1] && !path[2]) {
      await manager.closeAgent(path[1]);
      return json(res, 200, { closed: true, agentId: path[1] });
    }

    // GET /agents/:id/events — get timeline events
    if (method === "GET" && path[0] === "agents" && path[1] && path[2] === "events") {
      const since = parseInt(query.get("since") ?? "0", 10);
      const events = manager.getAgentEvents(path[1], since);
      return json(res, 200, { events, count: events.length });
    }

    // GET /agents/:id/stream — SSE stream
    if (method === "GET" && path[0] === "agents" && path[1] && path[2] === "stream") {
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
      });

      const agentId = path[1];
      const unsubscribe = manager.emitter.subscribe(agentId, (event: AgentEvent) => {
        res.write(`data: ${JSON.stringify(event)}\n\n`);
      });

      // Send heartbeat every 15s
      const heartbeat = setInterval(() => {
        res.write(": heartbeat\n\n");
      }, 15000);

      req.on("close", () => {
        unsubscribe();
        clearInterval(heartbeat);
      });

      return; // keep connection open
    }

    // GET /stream — global SSE stream (all agents)
    if (method === "GET" && path[0] === "stream" && !path[1]) {
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
      });

      const unsubscribe = manager.emitter.subscribeAll((event: AgentEvent) => {
        res.write(`data: ${JSON.stringify(event)}\n\n`);
      });

      const heartbeat = setInterval(() => {
        res.write(": heartbeat\n\n");
      }, 15000);

      req.on("close", () => {
        unsubscribe();
        clearInterval(heartbeat);
      });

      return; // keep connection open
    }

    // -- Chat endpoints -------------------------------------------------------

    // POST /chat/rooms — create chat room
    if (method === "POST" && path[0] === "chat" && path[1] === "rooms" && !path[2]) {
      const body = JSON.parse(await readBody(req));
      const room = chat.createRoom(body.name, body.purpose ?? "");
      return json(res, 201, room);
    }

    // GET /chat/rooms — list chat rooms
    if (method === "GET" && path[0] === "chat" && path[1] === "rooms" && !path[2]) {
      return json(res, 200, { rooms: chat.listRooms() });
    }

    // GET /chat/rooms/:id — get room info
    if (method === "GET" && path[0] === "chat" && path[1] === "rooms" && path[2] && !path[3]) {
      const room = chat.getRoom(path[2]);
      if (!room) return json(res, 404, { error: `Room not found: ${path[2]}` });
      return json(res, 200, {
        id: room.id,
        name: room.name,
        purpose: room.purpose,
        messageCount: room.messages.length,
        createdAt: room.createdAt,
      });
    }

    // DELETE /chat/rooms/:id — delete room
    if (method === "DELETE" && path[0] === "chat" && path[1] === "rooms" && path[2] && !path[3]) {
      const deleted = chat.deleteRoom(path[2]);
      return json(res, 200, { deleted, roomId: path[2] });
    }

    // POST /chat/rooms/:id/messages — post message
    if (method === "POST" && path[0] === "chat" && path[1] === "rooms" && path[2] && path[3] === "messages") {
      const body = JSON.parse(await readBody(req));
      const msg = chat.postMessage(
        path[2],
        body.senderId ?? "unknown",
        body.senderName ?? "unknown",
        body.content,
        body.mentions ?? [],
        body.replyTo,
      );
      if (!msg) return json(res, 404, { error: `Room not found: ${path[2]}` });
      return json(res, 201, msg);
    }

    // GET /chat/rooms/:id/messages — read messages
    if (method === "GET" && path[0] === "chat" && path[1] === "rooms" && path[2] && path[3] === "messages") {
      const limit = parseInt(query.get("limit") ?? "50", 10);
      const since = query.get("since") ?? undefined;
      const messages = chat.readMessages(path[2], limit, since);
      return json(res, 200, { messages, count: messages.length });
    }

    // GET /chat/rooms/:id/wait — wait for new message
    if (method === "GET" && path[0] === "chat" && path[1] === "rooms" && path[2] && path[3] === "wait") {
      const timeout = parseInt(query.get("timeout") ?? "30000", 10);
      const msg = await chat.waitForMessage(path[2], Math.min(timeout, 60000));
      if (!msg) return json(res, 204, null);
      return json(res, 200, msg);
    }

    // -- Permission endpoints --------------------------------------------------

    // GET /permissions — list pending permission requests
    if (method === "GET" && path[0] === "permissions" && !path[1]) {
      return json(res, 200, { pending: permissions.listPending() });
    }

    // GET /permissions/history — recent permission history
    if (method === "GET" && path[0] === "permissions" && path[1] === "history") {
      const limit = parseInt(query.get("limit") ?? "50", 10);
      return json(res, 200, { history: permissions.getHistory(limit) });
    }

    // POST /permissions/:id/respond — approve or deny
    if (method === "POST" && path[0] === "permissions" && path[1] && path[2] === "respond") {
      const body = JSON.parse(await readBody(req));
      const action = body.action as "approve" | "deny";
      if (!action || !["approve", "deny"].includes(action)) {
        return json(res, 400, { error: "action must be 'approve' or 'deny'" });
      }
      const success = permissions.respond(path[1], action, body.by ?? "user");
      if (!success) return json(res, 404, { error: `Permission request not found: ${path[1]}` });
      return json(res, 200, { responded: true, requestId: path[1], action });
    }

    // 404 fallback
    json(res, 404, { error: `Not found: ${method} /${path.join("/")}` });
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    log(`Request error: ${method} /${path.join("/")} — ${error}`);
    json(res, 500, { error });
  }
}

// -- Server startup ----------------------------------------------------------

/**
 * Check if a PID is alive.
 */
function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0); // signal 0 = existence check only
    return true;
  } catch {
    return false;
  }
}

function main(): void {
  // Parse --socket argument
  const socketArg = process.argv.indexOf("--socket");
  const socketPath = socketArg >= 0 && process.argv[socketArg + 1]
    ? process.argv[socketArg + 1]
    : `${process.env.HOME}/.revka/operator_mcp/session-manager.sock`;

  const pidPath = socketPath + ".pid";

  // ---- Singleton guardrail ----
  // If another session manager is alive, exit immediately.
  if (existsSync(pidPath)) {
    try {
      const existingPid = parseInt(readFileSync(pidPath, "utf-8").trim(), 10);
      if (existingPid && isProcessAlive(existingPid)) {
        log(`Another session manager is running (PID ${existingPid}), exiting`);
        process.exit(0);
      }
      log(`Stale PID file for dead process ${existingPid}, taking over`);
    } catch {
      // Corrupt PID file — proceed
    }
  }

  // Write our PID before doing anything else
  writeFileSync(pidPath, `${process.pid}\n`);

  // Clean up stale socket
  if (existsSync(socketPath)) {
    try {
      unlinkSync(socketPath);
    } catch {
      // ignore
    }
  }

  const server = createServer(handleRequest);

  server.listen(socketPath, async () => {
    log(`Session Manager listening on ${socketPath}`);
    log(`PID: ${process.pid}`);

    // Resume persisted sessions from previous run
    try {
      const resumed = await manager.resumePersistedSessions();
      if (resumed > 0) {
        log(`Resumed ${resumed} agent session(s)`);
      }
    } catch (err) {
      log(`Session resume failed: ${err}`);
    }
  });

  // Graceful shutdown
  const shutdown = () => {
    log("Shutting down...");
    server.close();
    try { unlinkSync(socketPath); } catch { /* ignore */ }
    try { unlinkSync(pidPath); } catch { /* ignore */ }
    process.exit(0);
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  // Cleanup on uncaught errors
  process.on("uncaughtException", (err) => {
    log(`Uncaught exception: ${err.message}`);
    log(err.stack ?? "");
    shutdown();
  });
}

main();
