import assert from "node:assert/strict";
import test from "node:test";
import { consumeSSE } from "../../frontend/stream.mjs";

const encoder = new TextEncoder();
const delta = (text) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;

function bodyFrom(chunks, { close = true } = {}) {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      if (close) controller.close();
    },
    cancel() { cancelled = true; },
  });
  return { body, cancelled: () => cancelled };
}

test("fragmented UTF-8, CRLF, comments and multiline SSE preserve text", async () => {
  const wire = ': heartbeat\r\n\r\ndata: {"choices":\r\ndata: [{"delta":{"content":"Tiếng Việt 🌱"}}]}\r\n\r\ndata: [DONE]\r\n\r\n';
  const bytes = encoder.encode(wire);
  const { body } = bodyFrom(Array.from(bytes, (byte) => Uint8Array.of(byte)));
  const pieces = [];
  assert.equal(await consumeSSE(body, (text) => pieces.push(text)), "Tiếng Việt 🌱");
  assert.deepEqual(pieces, ["Tiếng Việt 🌱"]);
  assert.equal(body.locked, false);
});

test("role and usage events do not turn into visible text", async () => {
  const { body } = bodyFrom([
    'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
    delta("Hello"),
    'data: {"choices":[],"usage":{"completion_tokens":1}}\n\n',
    delta(" world"),
    "data: [DONE]\n\n",
  ]);
  const pieces = [];
  assert.equal(await consumeSSE(body, (text) => pieces.push(text)), "Hello world");
  assert.deepEqual(pieces, ["Hello", " world"]);
});

test("text becomes observable before the success terminator arrives", { timeout: 2000 }, async () => {
  let source;
  const body = new ReadableStream({ start(controller) { source = controller; } });
  let firstText;
  const observed = new Promise((resolve) => { firstText = resolve; });
  const result = consumeSSE(body, firstText);
  source.enqueue(encoder.encode(delta("first")));
  assert.equal(await observed, "first");
  source.enqueue(encoder.encode("data: [DONE]\n\n"));
  assert.equal(await result, "first");
  assert.equal(body.locked, false);
});

test("DONE cancels unread input and releases the reader", async () => {
  const stream = bodyFrom([delta("done"), "data: [DONE]\n\n", delta("must not render")], { close: false });
  const pieces = [];
  assert.equal(await consumeSSE(stream.body, (text) => pieces.push(text)), "done");
  assert.deepEqual(pieces, ["done"]);
  assert.equal(stream.cancelled(), true);
  assert.equal(stream.body.locked, false);
});

for (const [name, event] of [
  ["named error", 'event: error\ndata: {"error":{"code":"upstream_timeout","message":"Generation timed out"}}\n\n'],
  ["JSON error", 'data: {"error":{"message":"Generation failed"}}\n\n'],
  ["malformed JSON", "data: {broken\n\n"],
  ["oversized event", `data: ${"x".repeat(65_537)}\n\n`],
]) {
  test(`${name} fails explicitly, keeps delivered text and closes its reader`, { timeout: 2000 }, async () => {
    const stream = bodyFrom([delta("partial"), event], { close: false });
    const pieces = [];
    await assert.rejects(consumeSSE(stream.body, (text) => pieces.push(text)));
    assert.deepEqual(pieces, ["partial"]);
    assert.equal(stream.cancelled(), true);
    assert.equal(stream.body.locked, false);
  });
}

test("an unterminated oversized event cannot accumulate without bound", { timeout: 2000 }, async () => {
  const stream = bodyFrom([`data: ${"x".repeat(65_537)}`], { close: false });
  await assert.rejects(consumeSSE(stream.body, () => {}));
  assert.equal(stream.cancelled(), true);
  assert.equal(stream.body.locked, false);
});

for (const wire of ["", delta("partial"), 'data: {"choices":']) {
  test(`EOF without DONE rejects (${JSON.stringify(wire)})`, async () => {
    const { body } = bodyFrom([wire]);
    await assert.rejects(consumeSSE(body, () => {}));
    assert.equal(body.locked, false);
  });
}

test("transport failure releases the reader and preserves already delivered text", async () => {
  let reads = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (reads++ === 0) controller.enqueue(encoder.encode(delta("partial")));
      else controller.error(new TypeError("connection lost"));
    },
  });
  const pieces = [];
  await assert.rejects(consumeSSE(body, (text) => pieces.push(text)), /connection lost/);
  assert.deepEqual(pieces, ["partial"]);
  assert.equal(body.locked, false);
});

test("render callback failure also cancels and unlocks the stream", async () => {
  const stream = bodyFrom([delta("text")], { close: false });
  await assert.rejects(consumeSSE(stream.body, () => { throw new Error("render failed"); }), /render failed/);
  assert.equal(stream.cancelled(), true);
  assert.equal(stream.body.locked, false);
});

test("AbortSignal closes a blocked reader even when the source ignores fetch abort", { timeout: 2000 }, async () => {
  const stream = bodyFrom([delta("partial")], { close: false });
  const controller = new AbortController();
  let observed;
  const firstText = new Promise((resolve) => { observed = resolve; });
  const result = consumeSSE(stream.body, observed, controller.signal);
  assert.equal(await firstText, "partial");
  controller.abort();
  await assert.rejects(result, { name: "AbortError" });
  assert.equal(stream.cancelled(), true);
  assert.equal(stream.body.locked, false);
});

test("an already aborted signal cancels input without rendering text", async () => {
  const stream = bodyFrom([delta("must not render")], { close: false });
  const controller = new AbortController();
  controller.abort();
  const pieces = [];
  await assert.rejects(consumeSSE(stream.body, (text) => pieces.push(text), controller.signal), { name: "AbortError" });
  assert.deepEqual(pieces, []);
  assert.equal(stream.cancelled(), true);
  assert.equal(stream.body.locked, false);
});
