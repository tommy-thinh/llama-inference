import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { mountChat } from "../../frontend/app.mjs";

const encoder = new TextEncoder();
const delta = (text) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;

// Only the browser surface used by this small form; innerHTML deliberately fails.
class Element extends EventTarget {
  constructor(tagName = "div") {
    super();
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.value = "";
    this.disabled = false;
    this.className = "";
    this.attributes = {};
    this._text = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.dataset = {};
  }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent ?? child).join(""); }
  set innerHTML(_) { throw new Error("Chat content must use textContent, never innerHTML"); }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this._text = ""; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus() { this.focused = true; }
  requestSubmit() { this.dispatchEvent(new Event("submit", { cancelable: true })); }
}

function fixture(fetchImpl) {
  const elements = Object.fromEntries([
    "chat-form", "prompt", "messages", "status", "send", "stop", "max-tokens", "temperature",
  ].map((id) => [id, new Element(id === "chat-form" ? "form" : "div")]));
  elements["max-tokens"].value = "1024";
  elements.temperature.value = "0.7";
  const document = {
    getElementById(id) { assert.ok(elements[id], `Unknown element ID: ${id}`); return elements[id]; },
    createElement(tag) { return new Element(tag); },
    createTextNode(text) { return { textContent: text }; },
  };
  mountChat(document, fetchImpl);
  return elements;
}

function submit(ui, prompt) {
  ui.prompt.value = prompt;
  ui["chat-form"].requestSubmit();
}

function keyboard(ui, properties = {}) {
  const event = new Event("keydown", { cancelable: true });
  Object.assign(event, { key: "Enter", shiftKey: false, isComposing: false, ...properties });
  ui.prompt.dispatchEvent(event);
  return event;
}

async function until(predicate, description) {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`Timed out waiting for ${description}`);
}

function success(text) {
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(delta(text) + "data: [DONE]\n\n"));
      controller.close();
    },
  }), { headers: { "content-type": "text/event-stream" } });
}

function pendingStream(signal, text = "partial") {
  let source;
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { source = controller; },
    cancel() { cancelled = true; },
  });
  source.enqueue(encoder.encode(delta(text)));
  signal?.addEventListener("abort", () => source.error(new DOMException("Stopped", "AbortError")), { once: true });
  return { body, source, cancelled: () => cancelled, response: new Response(body, { headers: { "content-type": "text/event-stream" } }) };
}

test("empty prompts do not fetch and idle controls are usable", async () => {
  let calls = 0;
  const ui = fixture(() => { calls++; return Promise.resolve(success("unused")); });
  submit(ui, " \n\t ");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls, 0);
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.stop.disabled, true);
});

test("streaming uses POST, safe text, visible busy state, and completed history", async () => {
  const requests = [];
  let active;
  const ui = fixture(async (url, options) => {
    requests.push({ url, options, data: JSON.parse(options.body) });
    if (requests.length === 1) {
      active = pendingStream(options.signal, '<img src=x onerror="bad()">');
      return active.response;
    }
    return success("second answer");
  });
  submit(ui, "<script>first</script>");
  await until(() => ui.messages.textContent.includes("<img"), "progressive assistant text");
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.stop.disabled, false);
  assert.ok(ui.status.textContent.trim(), "Busy status should be visible");
  assert.ok(ui.messages.textContent.includes("<script>first</script>"));
  assert.equal(requests[0].url, "/api/chat");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].data.max_tokens, 1024);
  assert.equal(requests[0].data.temperature, 0.7);
  assert.ok(requests[0].options.signal instanceof AbortSignal);
  active.source.enqueue(encoder.encode("data: [DONE]\n\n"));
  await until(() => !ui.send.disabled, "completion controls");
  assert.equal(ui.stop.disabled, true);
  assert.equal(active.body.locked, false);
  submit(ui, "second");
  await until(() => requests.length === 2 && !ui.send.disabled, "second completed request");
  assert.deepEqual(requests[1].data.messages, [
    { role: "user", content: "<script>first</script>" },
    { role: "assistant", content: '<img src=x onerror="bad()">' },
    { role: "user", content: "second" },
  ]);
});

test("Stop aborts the request, preserves partial text, restores controls and allows clean retry", async () => {
  const requests = [];
  let active;
  const ui = fixture(async (_, options) => {
    requests.push(options);
    if (requests.length === 1) {
      active = pendingStream(null);
      return active.response;
    }
    return success("retry worked");
  });
  submit(ui, "stopped prompt");
  await until(() => ui.messages.textContent.includes("partial"), "first streamed text");
  ui.stop.dispatchEvent(new Event("click"));
  await until(() => !ui.send.disabled && !active.body.locked, "stop cleanup");
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(active.cancelled(), true);
  assert.equal(active.body.locked, false);
  assert.equal(ui.stop.disabled, true);
  assert.match(ui.status.textContent, /stop|cancel/i);
  assert.ok(ui.messages.textContent.includes("partial"));
  submit(ui, "retry");
  await until(() => requests.length === 2 && !ui.send.disabled, "retry completion");
  assert.deepEqual(JSON.parse(requests[1].body).messages, [{ role: "user", content: "retry" }]);
});

test("Stop also aborts while still waiting for response headers", async () => {
  let signal;
  const ui = fixture((_, options) => new Promise((_, reject) => {
    signal = options.signal;
    signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true });
  }));
  submit(ui, "waiting");
  await until(() => signal !== undefined, "fetch start");
  assert.equal(ui.stop.disabled, false);
  ui.stop.dispatchEvent(new Event("click"));
  await until(() => !ui.send.disabled, "pre-header cancellation");
  assert.equal(signal.aborted, true);
  assert.equal(ui.stop.disabled, true);
  assert.match(ui.status.textContent, /stop|cancel/i);
});

for (const [name, fail] of [
  ["network rejection", () => Promise.reject(new TypeError("Network unavailable"))],
  ["HTTP error", () => Promise.resolve(new Response("unavailable", { status: 503 }))],
  ["premature EOF", () => Promise.resolve(new Response(delta("unfinished"), { headers: { "content-type": "text/event-stream" } }))],
  ["named stream error", () => Promise.resolve(new Response(delta("unfinished") + 'event: error\ndata: {"error":{"message":"Generation timed out"}}\n\n', { headers: { "content-type": "text/event-stream" } }))],
]) {
  test(`${name} reports failure, restores controls, and excludes the failed turn on retry`, async () => {
    const requests = [];
    const ui = fixture((_, options) => {
      requests.push(JSON.parse(options.body));
      return requests.length === 1 ? fail() : Promise.resolve(success("recovered"));
    });
    submit(ui, "failed prompt");
    await until(() => requests.length === 1 && !ui.send.disabled, "failure cleanup");
    assert.equal(ui.stop.disabled, true);
    assert.match(ui.status.textContent, /error|fail|unavailable|timed out|incomplete|ended|503|network/i);
    if (name.includes("EOF") || name.includes("stream")) assert.ok(ui.messages.textContent.includes("unfinished"));
    submit(ui, "fresh prompt");
    await until(() => requests.length === 2 && !ui.send.disabled, "recovery completion");
    assert.deepEqual(requests[1].messages, [{ role: "user", content: "fresh prompt" }]);
  });
}

test("a failed later turn preserves successful context for the next request", async () => {
  const requests = [];
  const ui = fixture((_, options) => {
    requests.push(JSON.parse(options.body));
    if (requests.length === 2) return Promise.reject(new TypeError("Network unavailable"));
    return Promise.resolve(success("completed answer"));
  });
  for (const [index, prompt] of ["completed prompt", "failed prompt", "retry prompt"].entries()) {
    submit(ui, prompt);
    await until(() => requests.length === index + 1 && !ui.send.disabled, `request ${index}`);
  }
  assert.deepEqual(requests[2].messages, [
    { role: "user", content: "completed prompt" },
    { role: "assistant", content: "completed answer" },
    { role: "user", content: "retry prompt" },
  ]);
});

test("Enter submits; Shift+Enter and IME composition do not submit", async () => {
  let calls = 0;
  const ui = fixture(async () => { calls++; return success("ok"); });
  ui.prompt.value = "hello";
  assert.equal(keyboard(ui, { shiftKey: true }).defaultPrevented, false);
  assert.equal(keyboard(ui, { isComposing: true }).defaultPrevented, false);
  assert.equal(calls, 0);
  assert.equal(keyboard(ui).defaultPrevented, true);
  await until(() => calls === 1 && !ui.send.disabled, "Enter completion");
});

test("duplicate submits during generation start only one fetch", async () => {
  let calls = 0;
  let active;
  const ui = fixture(async (_, options) => { calls++; active = pendingStream(options.signal); return active.response; });
  submit(ui, "first");
  submit(ui, "second");
  await until(() => ui.messages.textContent.includes("partial"), "active stream");
  assert.equal(calls, 1);
  ui.stop.dispatchEvent(new Event("click"));
  await until(() => !ui.send.disabled && !active.body.locked, "cleanup");
  assert.equal(active.body.locked, false);
});

test("page advertises the requested default and inclusive output limits", () => {
  const html = readFileSync(new URL("../../frontend/index.html", import.meta.url), "utf8");
  const input = html.match(/<input\b[^>]*\bid="max-tokens"[^>]*>/)?.[0];
  assert.ok(input, "The output limit control must exist");
  const attribute = (name) => input.match(new RegExp(`\\b${name}="([^"]*)"`))?.[1];
  assert.equal(attribute("type"), "number");
  assert.equal(attribute("min"), "1");
  assert.equal(attribute("max"), "2048");
  assert.equal(attribute("step"), "1");
  assert.equal(attribute("value"), "1024");
});

for (const maxTokens of [1, 513, 1024, 2048]) {
  test(`output limit ${maxTokens} reaches the API unchanged`, async () => {
    const requests = [];
    const ui = fixture(async (_, options) => { requests.push(JSON.parse(options.body)); return success("answer"); });
    ui["max-tokens"].value = String(maxTokens);
    submit(ui, "prompt");
    await until(() => requests.length === 1 && !ui.send.disabled, "completion at allowed output limit");
    assert.equal(requests[0].max_tokens, maxTokens);
  });
}

for (const [field, value] of [
  ["prompt", "x".repeat(4001)],
  ["max-tokens", "2049"],
  ["max-tokens", "0"],
  ["temperature", "2.1"],
]) {
  test(`invalid ${field} is visible and starts no request`, async () => {
    let calls = 0;
    const ui = fixture(async () => { calls++; return success("unused"); });
    ui.prompt.value = "prompt";
    ui[field].value = value;
    ui["chat-form"].requestSubmit();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(calls, 0);
    assert.ok(ui.status.textContent.trim(), "Validation should be visible");
    assert.equal(ui.send.disabled, false);
  });
}

test("full history reports an error without silently deleting earlier context", async () => {
  const requests = [];
  const ui = fixture(async (_, options) => { requests.push(JSON.parse(options.body)); return success("answer"); });
  for (let index = 0; index < 8; index++) {
    submit(ui, `question ${index}`);
    await until(() => requests.length === index + 1 && !ui.send.disabled, `turn ${index}`);
  }
  assert.equal(requests[7].messages.length, 15);
  submit(ui, "ninth question");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(requests.length, 8);
  assert.match(ui.status.textContent, /history|conversation|limit|full|reload|refresh|16/i);
});

test("total history character bound is enforced before the next fetch", async () => {
  let calls = 0;
  const ui = fixture(async () => { calls++; return success("a".repeat(4000)); });
  submit(ui, "q".repeat(4000));
  await until(() => calls === 1 && !ui.send.disabled, "large completed turn");
  submit(ui, "one more");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls, 1);
  assert.ok(ui.status.textContent.trim());
});
