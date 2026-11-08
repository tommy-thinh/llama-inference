# 05 — HTTP streaming and cancellation

A POST request sends the conversation and generation parameters as JSON. The
response is an HTTP body containing Server-Sent Events (SSE): fields separated
by newlines, with a blank line ending each event. This is one-way output after
one request, so it does not need a persistent bidirectional WebSocket protocol.
Fetch supports the POST body and AbortController; EventSource is oriented around
GET subscriptions.

```text
data: {"choices":[{"delta":{"content":"Hello"}}]}

data: {"choices":[{"delta":{"content":"!"}}]}

data: [DONE]

```

Network chunks, UTF-8 characters, SSE frames, and model tokens have independent
boundaries. The backend and frontend retain only incomplete frames while
processing completed events progressively. Their 65,536-character event bounds
also apply before an unterminated line reaches EOF. Role and usage events may
carry no visible text. A valid terminator, rather than a closed socket alone,
marks successful completion.

The backend streams through one application-scoped HTTPX AsyncClient. It opens
one response per request and closes it in a cancellation-shielded `finally`
block. Its StreamingResponse also listens for ASGI disconnects while awaiting
headers or tokens; this works on ASGI 2.3 and 2.4 instead of waiting for a failed
send to reveal that the browser has gone away. Generator closure is explicit
even if downstream sending is interrupted.

Stop calls AbortController, cancels the browser body reader, and restores the UI.
The closed downstream connection cancels the backend producer, which closes the
upstream HTTP response. llama-server can then cancel its request and release the
single generation slot. No Python code reloads model weights between requests.

## Failures have two forms

Schema errors occur before streaming starts and receive HTTP 422. Valid requests
start an SSE response, so an unavailable server, rejected context, timeout,
transport failure, or malformed stream uses an explicit terminal error event:

```text
event: error
data: {"error":{"code":"upstream_timeout","message":"Generation timed out. Please try again."}}

```

An error ends the stream without a success `[DONE]`. The UI retains partial text,
shows the error, and excludes that incomplete turn from later model input.
Retry is a deliberate new submission; the backend does not silently repeat a
generation. Error messages omit raw upstream bodies and internal exception text.

The connect timeout bounds connection establishment and pool waiting. The read
timeout bounds idle gaps. A separate total deadline bounds the entire operation,
including a peer that keeps sending small pieces without ever finishing. Backend
timeouts are configurable through `LLAMA_API_CONNECT_TIMEOUT`,
`LLAMA_API_READ_TIMEOUT`, and `LLAMA_API_TOTAL_TIMEOUT` (positive, at most 300 s).
Their defaults are 5, 30, and 120 seconds.

Terminal errors are emitted only after the upstream response is closed. The
response producer grants at most 100 ms beyond the total deadline to flush a
terminal frame, then cancels a blocked send. This also bounds the final empty
HTTP body send after successful `[DONE]`; a client that stops reading cannot
retain the upstream response or an indefinitely pending response task.

## References

- [HTTPX asynchronous streaming and response ownership](https://www.python-httpx.org/async/)
- [FastAPI streaming response behavior](https://fastapi.tiangolo.com/advanced/custom-response/)
- [Fetch streams](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch#streaming_the_response_body)
- [AbortController](https://developer.mozilla.org/en-US/docs/Web/API/AbortController)

The API contract and tests are in [architecture](04-fastapi-architecture.md) and
[frontend communication](06-frontend-backend-communication.md).
