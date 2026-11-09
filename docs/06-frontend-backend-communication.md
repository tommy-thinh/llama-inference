# 06 — Browser and backend communication

The UI is a single responsive HTML page with local CSS and vanilla JavaScript.
It uses `fetch('/api/conversations/{id}/chat', {method: 'POST', body, signal})` and reads the response
body incrementally. Requests stay on the same origin with the session cookie. The server builds
model history; the browser sends only the current content and generation settings. Chat text is inserted with
`textContent`, so user/model HTML is displayed literally.

## Observable frontend contract

- A prompt textarea, Send button, Stop Generation button, message log, visible
  status/error area, and bounded output-token/temperature inputs are present.
  Output defaults to 1,024 tokens and accepts 1–2,048 tokens. The native default
  context is 4,096 tokens; prompt and output share this context budget.
- Empty or whitespace-only prompts do not start a request. While generating,
  Send is disabled and Stop is enabled. Enter sends; Shift+Enter inserts a line
  break; IME composition must not accidentally submit.
- User and assistant messages are distinct. Text deltas appear progressively;
  role/usage-only events are ignored. Failures preserve partial output and show
  an explicit message. Network/HTTP errors and EOF without `[DONE]` are failures.
- Stop uses AbortController and cancels the active body reader. It preserves
  partial text, labels the request stopped, and restores controls. A new request
  can be submitted afterward. All accepted turns are saved in PostgreSQL;
  unsuccessful/stopped turns stay visible with state labels but are excluded from
  future model input. Refresh retrieves saved messages.
- The frontend parser supports fragmented UTF-8, CRLF, SSE comments and multiline
  data, `[DONE]`, and both named error events and JSON error payloads. Event and
  input bounds prevent accidental unlimited accumulation. Completed model history
  must fit the backend's 16-message/8,000-character bound; a full history produces a
  visible error rather than silently dropping context.
- Page bootstrap establishes an anonymous cookie session, lists conversations,
  and restores the latest (or creates the first). New Conversation and the
  selector switch saved histories. There is no account registration or external
  CDN/font/script dependency. See [session semantics](08-sessions-and-persistence.md).

`frontend/stream.mjs` exports `consumeSSE(body, onText)`, which returns the joined
assistant text after a valid terminator and closes its reader on every exit.
An optional third AbortSignal cancels the reader immediately, including when a
test transport does not connect Fetch cancellation to its response body.
`frontend/app.mjs` exports `mountChat(document, fetchImpl=globalThis.fetch)` and
mounts itself in a browser. Element IDs are `chat-form`, `prompt`, `messages`,
`status`, `send`, `stop`, `max-tokens`, `temperature`, `conversations`, and
`new-conversation`. These module boundaries
support Node's built-in test runner without installing a frontend framework or
DOM library. Tests check observable state, safe rendering, stop/retry, and parsing.

Use two terminals after `conda activate llama`: `python -m inference.server`
owns the model process; `source .runtime/database.env` then `alembic upgrade head`
and `python -m backend` serve the page at
`http://127.0.0.1:8000`. On a VPS, forward that backend port over SSH and open the
forwarded local address. Ctrl+C stops each foreground service.

## Historical Phase 2 verification

The counts below describe the committed Phase 2 snapshot, before persistence.
Current Phase 3 verification is in [project status](project-status.md).

All 33 Node behavior tests pass through the pytest bridge. The full live suite
passes 260 tests, including the actual backend-to-native streaming smoke. A
separate real TCP check fetched the page and all assets, used Node Fetch and the
production SSE parser for progressive output, cancelled after two visible chunks,
and recovered with arithmetic answer `4` in about 0.48 s. Native cancellation was
logged and both owned processes were reaped. See the
[evidence](evidence/phase2-verification.json). These checks exercised behavior and
network transport; no graphical browser or visual-layout automation was run.
