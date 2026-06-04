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
export {};
