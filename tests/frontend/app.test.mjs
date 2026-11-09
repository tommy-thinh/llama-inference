import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { mountChat } from "../../frontend/app.mjs";

const encoder = new TextEncoder();
const delta = (text) => `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

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
    "chat-form", "prompt", "messages", "status", "send", "stop", "max-tokens", "temperature", "new-conversation", "conversations",
  ].map((id) => [id, new Element(id === "chat-form" ? "form" : id === "conversations" ? "select" : "div")]));
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

function conversation(id, messages = [], title = `Conversation ${id}`) {
  return { id, title, created_at: "2026-11-09T00:00:00Z", updated_at: "2026-11-09T00:00:00Z", messages, active_request: null, next_before_position: null };
}
function message(role, content, status = "completed", position = 1) {
  return { id: `message-${position}`, role, content, status, position, request_id: "request-1", created_at: "2026-11-09T00:00:00Z" };
}

// A session-owned API, independent of any persistence implementation or database.
function api({ initial = [conversation("first")], onChat, intercept } = {}) {
  const state = { conversations: structuredClone(initial), calls: [], chats: [] };
  state.fetch = async (url, options = {}) => {
    const call = { url, options, method: options.method ?? "GET", data: options.body ? JSON.parse(options.body) : undefined };
    state.calls.push(call);
    const overridden = intercept?.(call, state);
    if (overridden !== undefined) return overridden;
    const path = url.split("?")[0];
    if (path === "/api/session" && call.method === "POST") return json({ status: "ok", expires_at: "2026-12-09T00:00:00Z" });
    if (path === "/api/conversations" && call.method === "GET") return json({ items: state.conversations.map(({ messages, active_request, next_before_position, ...meta }) => meta), limit: 20, offset: 0 });
    if (path === "/api/conversations" && call.method === "POST") {
      const created = conversation(`new-${state.conversations.length}`, [], "New conversation");
      state.conversations.unshift(created);
      return json(created, 201);
    }
    const matched = path.match(/^\/api\/conversations\/([^/]+)(\/chat)?$/);
    assert.ok(matched, `Unexpected endpoint ${call.method} ${url}`);
    const selected = state.conversations.find(({ id }) => id === matched[1]);
    if (!selected) return json({ detail: "Conversation not found" }, 404);
    if (!matched[2] && call.method === "GET") return json(selected);
    assert.equal(call.method, "POST");
    assert.equal(matched[2], "/chat");
    state.chats.push(call);
    if (onChat) return onChat(call, selected, state);
    selected.messages.push(message("user", call.data.content, "completed", selected.messages.length + 1));
    selected.messages.push(message("assistant", "saved answer", "completed", selected.messages.length + 1));
    return success("saved answer");
  };
  return state;
}

function submit(ui, prompt) { ui.prompt.value = prompt; ui["chat-form"].requestSubmit(); }
function select(ui, id) { ui.conversations.value = id; ui.conversations.dispatchEvent(new Event("change")); }
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
async function ready(ui, id = "first") {
  await until(() => !ui.send.disabled && ui.conversations.value === id, "session and conversation bootstrap");
}
function success(text) { return new Response(delta(text) + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } }); }
function pendingStream(text = "partial") {
  let source;
  let cancelled = false;
  const body = new ReadableStream({ start(controller) { source = controller; }, cancel() { cancelled = true; } });
  source.enqueue(encoder.encode(delta(text)));
  return { body, source, cancelled: () => cancelled, response: new Response(body, { headers: { "content-type": "text/event-stream" } }) };
}
function deferred() {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
}

test("bootstrap reuses the session and restores newest persisted history with literal state labels", async () => {
  const saved = [message("user", "<script>stored</script>"), message("assistant", "<img src=x onerror=bad()>", "partial", 2)];
  const server = api({ initial: [conversation("newest", saved, "<b>Newest</b>"), conversation("older")] });
  const ui = fixture(server.fetch);
  await ready(ui, "newest");
  const sessionIndex = server.calls.findIndex(({ method, url }) => method === "POST" && url === "/api/session");
  const listIndex = server.calls.findIndex(({ method, url }) => method === "GET" && url.split("?")[0] === "/api/conversations");
  const detailIndex = server.calls.findIndex(({ method, url }) => method === "GET" && url === "/api/conversations/newest");
  assert.ok(sessionIndex >= 0 && listIndex > sessionIndex && detailIndex > listIndex, "Bootstrap must establish a session, list its conversations, then restore newest history");
  assert.match(ui.messages.textContent, /<script>stored<\/script>/);
  assert.match(ui.messages.textContent, /<img src=x onerror=bad\(\)>/);
  assert.match(ui.messages.textContent, /partial/i);
  assert.match(ui.conversations.textContent, /<b>Newest<\/b>/);
  assert.equal(ui.stop.disabled, true);
  for (const { options } of server.calls) assert.ok(options.credentials === undefined || options.credentials === "same-origin", "Fetch must preserve same-origin cookies");
});

test("reload restores saved history without creating a replacement conversation", async () => {
  const server = api({ initial: [conversation("first", [message("assistant", "saved across reload", "cancelled")])] });
  const first = fixture(server.fetch);
  await ready(first);
  const reloaded = fixture(server.fetch);
  await ready(reloaded);
  assert.match(reloaded.messages.textContent, /saved across reload/);
  assert.match(reloaded.messages.textContent, /cancelled/i);
  assert.equal(server.calls.filter(({ method, url }) => method === "POST" && url === "/api/conversations").length, 0);
});

test("an empty session creates its first conversation and New Conversation selects another saved empty one", async () => {
  const server = api({ initial: [] });
  const ui = fixture(server.fetch);
  await ready(ui, "new-0");
  submit(ui, "first prompt");
  await until(() => server.chats.length === 1 && !ui.send.disabled, "saved first answer");
  ui["new-conversation"].dispatchEvent(new Event("click"));
  await ready(ui, "new-1");
  assert.equal(server.conversations.length, 2);
  assert.equal(ui.messages.textContent.includes("first prompt"), false);
  assert.equal(ui.messages.textContent.includes("saved answer"), false);
  submit(ui, "another conversation");
  await until(() => server.chats.length === 2 && !ui.send.disabled, "new conversation request");
  assert.equal(server.chats[1].url, "/api/conversations/new-1/chat");
  assert.deepEqual(server.chats[1].data, { content: "another conversation", max_tokens: 1024, temperature: 0.7 });
});

test("selecting an owned conversation replaces the visible history and shows failed/cancelled states", async () => {
  const server = api({ initial: [conversation("first", [message("assistant", "old answer")]), conversation("second", [message("assistant", "failed partial", "failed"), message("assistant", "stopped partial", "cancelled", 2)])] });
  const ui = fixture(server.fetch);
  await ready(ui);
  select(ui, "second");
  await until(() => ui.messages.textContent.includes("stopped partial") && !ui.send.disabled, "selected saved history");
  assert.equal(ui.messages.textContent.includes("old answer"), false);
  assert.match(ui.messages.textContent, /failed/i);
  assert.match(ui.messages.textContent, /cancelled/i);
});

test("empty prompts do not generate and idle controls remain usable", async () => {
  const server = api();
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, " \n\t ");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(server.chats.length, 0);
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.stop.disabled, true);
});

test("streaming sends content only, safely renders progressive text, and refreshes durable state at completion", async () => {
  let active;
  let saved;
  const server = api({ onChat(call, selected) {
    saved = selected;
    selected.messages = [message("user", call.data.content), message("assistant", '<img src=x onerror="bad()">', "partial", 2)];
    active = pendingStream('<img src=x onerror="bad()">');
    return active.response;
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "<script>first</script>");
  await until(() => ui.messages.textContent.includes("<img"), "progressive assistant text");
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.stop.disabled, false);
  assert.equal(ui.conversations.disabled, true);
  assert.equal(ui["new-conversation"].disabled, true);
  assert.ok(ui.status.textContent.trim(), "Busy status must be visible");
  assert.ok(ui.messages.textContent.includes("<script>first</script>"));
  const request = server.chats[0];
  assert.equal(request.url, "/api/conversations/first/chat");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(request.data, { content: "<script>first</script>", max_tokens: 1024, temperature: 0.7 });
  assert.ok(request.options.signal instanceof AbortSignal);
  const before = server.calls.length;
  saved.messages[1].content = "durably refreshed answer";
  saved.messages[1].status = "completed";
  active.source.enqueue(encoder.encode("data: [DONE]\n\n"));
  await until(() => !ui.send.disabled && ui.messages.textContent.includes("durably refreshed answer"), "durable state refresh");
  assert.ok(server.calls.slice(before).some(({ url, method }) => url === "/api/conversations/first" && method === "GET"));
  assert.equal(ui.stop.disabled, true);
  assert.equal(active.body.locked, false);
});

test("Stop aborts Fetch, preserves persisted partial text, restores controls and retries without resubmitting history", async () => {
  let active;
  const server = api({ onChat(call, selected, state) {
    if (state.chats.length > 1) return success("retry worked");
    selected.messages = [message("user", call.data.content), message("assistant", "partial", "partial", 2)];
    call.options.signal.addEventListener("abort", () => { selected.messages[1].status = "cancelled"; }, { once: true });
    active = pendingStream();
    return active.response;
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "stopped prompt");
  await until(() => ui.messages.textContent.includes("partial"), "partial generation");
  ui.stop.dispatchEvent(new Event("click"));
  await until(() => !ui.send.disabled && !active.body.locked, "stop cleanup");
  assert.equal(server.chats[0].options.signal.aborted, true);
  assert.equal(active.cancelled(), true);
  assert.equal(ui.stop.disabled, true);
  assert.match(ui.messages.textContent, /partial/);
  assert.match(ui.messages.textContent + ui.status.textContent, /cancel|stop/i);
  submit(ui, "retry");
  await until(() => server.chats.length === 2 && !ui.send.disabled, "retry completion");
  assert.deepEqual(server.chats[1].data, { content: "retry", max_tokens: 1024, temperature: 0.7 });
});

test("Stop also aborts while waiting for response headers", async () => {
  let signal;
  const server = api({ onChat(call) {
    signal = call.options.signal;
    return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true }));
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
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
  test(`${name} restores controls, refreshes saved failure state and keeps retry content-only`, async () => {
    const server = api({ onChat(call, selected, state) {
      if (state.chats.length > 1) return success("recovered");
      selected.messages = [message("user", call.data.content), message("assistant", "saved unfinished", "failed", 2)];
      return fail();
    } });
    const ui = fixture(server.fetch);
    await ready(ui);
    submit(ui, "failed prompt");
    await until(() => server.chats.length === 1 && !ui.send.disabled, "failure cleanup");
    assert.equal(ui.stop.disabled, true);
    assert.match(ui.status.textContent, /error|fail|unavailable|timed out|incomplete|ended|503|network/i);
    assert.match(ui.messages.textContent, /saved unfinished/);
    assert.match(ui.messages.textContent, /failed/i);
    submit(ui, "fresh prompt");
    await until(() => server.chats.length === 2 && !ui.send.disabled, "recovery completion");
    assert.deepEqual(server.chats[1].data, { content: "fresh prompt", max_tokens: 1024, temperature: 0.7 });
  });
}

test("409 while a cancelled request finalizes reports the conflict and preserves that saved partial turn", async () => {
  const server = api({ initial: [conversation("first", [message("assistant", "cancelled saved text", "cancelled")])], onChat() { return json({ error: { code: "conversation_busy", message: "Conversation already has an active request" } }, 409); } });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "retry too soon");
  await until(() => server.chats.length === 1 && !ui.send.disabled, "conflict cleanup");
  assert.match(ui.status.textContent, /409|active|busy|error|fail/i);
  assert.match(ui.messages.textContent, /cancelled saved text/);
  assert.equal(ui.stop.disabled, true);
});

test("Enter submits; Shift+Enter and IME composition do not submit", async () => {
  const server = api();
  const ui = fixture(server.fetch);
  await ready(ui);
  ui.prompt.value = "hello";
  assert.equal(keyboard(ui, { shiftKey: true }).defaultPrevented, false);
  assert.equal(keyboard(ui, { isComposing: true }).defaultPrevented, false);
  assert.equal(server.chats.length, 0);
  assert.equal(keyboard(ui).defaultPrevented, true);
  await until(() => server.chats.length === 1 && !ui.send.disabled, "Enter completion");
});

test("duplicate submits during generation start only one persistent request", async () => {
  let active;
  const server = api({ onChat() { active = pendingStream(); return active.response; } });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "first");
  submit(ui, "second");
  await until(() => ui.messages.textContent.includes("partial"), "active stream");
  assert.equal(server.chats.length, 1);
  ui.stop.dispatchEvent(new Event("click"));
  await until(() => !ui.send.disabled && !active.body.locked, "cleanup");
});

test("page exposes conversation controls and requested inclusive output limits", () => {
  const html = readFileSync(new URL("../../frontend/index.html", import.meta.url), "utf8");
  for (const id of ["new-conversation", "conversations"]) assert.match(html, new RegExp(`id="${id}"`));
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
  test(`output limit ${maxTokens} reaches the persistent API unchanged`, async () => {
    const server = api();
    const ui = fixture(server.fetch);
    await ready(ui);
    ui["max-tokens"].value = String(maxTokens);
    submit(ui, "prompt");
    await until(() => server.chats.length === 1 && !ui.send.disabled, "completion at allowed output limit");
    assert.equal(server.chats[0].data.max_tokens, maxTokens);
  });
}

for (const [field, value] of [["prompt", "x".repeat(4001)], ["max-tokens", "2049"], ["max-tokens", "0"], ["temperature", "2.1"]]) {
  test(`invalid ${field} is visible and starts no generation`, async () => {
    const server = api();
    const ui = fixture(server.fetch);
    await ready(ui);
    ui.prompt.value = "prompt";
    ui[field].value = value;
    ui["chat-form"].requestSubmit();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(server.chats.length, 0);
    assert.ok(ui.status.textContent.trim(), "Validation must be visible");
    assert.equal(ui.send.disabled, false);
  });
}

test("failed bootstrap displays a safe visible error and never submits a transient chat", async () => {
  const server = api({ intercept({ url }) { if (url === "/api/session") return json({ error: { message: "Persistence unavailable" } }, 503); } });
  const ui = fixture(server.fetch);
  await until(() => /error|fail|unavailable|503/i.test(ui.status.textContent), "bootstrap error");
  submit(ui, "cannot save");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(server.chats.length, 0);
  assert.equal(server.calls.some(({ url }) => url === "/api/chat"), false);
  assert.equal(ui.stop.disabled, true);
});

test("ownership-safe 404 selection errors do not display another conversation's content or send under its ID", async () => {
  const server = api({ initial: [conversation("first", [message("assistant", "owned saved answer")]), conversation("unavailable")], intercept({ url }) {
    if (url.startsWith("/api/conversations/unavailable")) return json({ detail: "Conversation not found" }, 404);
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
  select(ui, "unavailable");
  await until(() => /error|fail|404|not found/i.test(ui.status.textContent), "ownership-safe error");
  submit(ui, "after failed switch");
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(server.calls.some(({ url, method }) => method === "POST" && url.includes("unavailable")), false, "A failed switch must not make that conversation sendable");
  select(ui, "first");
  await until(() => !ui.send.disabled && ui.messages.textContent.includes("owned saved answer"), "owned selection recovery");
});

test("a delayed older conversation response cannot overwrite a newer selected history", async () => {
  const older = deferred();
  let delayed = false;
  const server = api({ initial: [conversation("first"), conversation("slow", [message("assistant", "stale slow history")]), conversation("latest", [message("assistant", "latest chosen history")])], intercept({ url }) {
    if (url === "/api/conversations/slow") { delayed = true; return older.promise; }
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
  select(ui, "slow");
  await until(() => delayed, "delayed selection request");
  select(ui, "latest");
  await until(() => ui.messages.textContent.includes("latest chosen history"), "newer selection loaded first");
  older.resolve(json(server.conversations.find(({ id }) => id === "slow")));
  await new Promise((resolve) => setTimeout(resolve, 10));
  await until(() => !ui.send.disabled, "selection requests completed");
  assert.equal(ui.conversations.value, "latest");
  assert.match(ui.messages.textContent, /latest chosen history/);
  assert.equal(ui.messages.textContent.includes("stale slow history"), false);
});

test("generation cannot start before session bootstrap finishes", async () => {
  const session = deferred();
  const server = api({ intercept({ url }) { if (url === "/api/session") return session.promise; } });
  const ui = fixture(server.fetch);
  submit(ui, "too early");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(server.chats.length, 0);
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.stop.disabled, true);
  session.resolve(json({ status: "ok", expires_at: "2026-12-09T00:00:00Z" }));
  await ready(ui);
});

test("failed New Conversation restores controls and keeps the previous saved history", async () => {
  const server = api({ initial: [conversation("first", [message("assistant", "preserved saved history")])], intercept({ url, method }) {
    if (url === "/api/conversations" && method === "POST") return json({ error: { message: "Persistence unavailable" } }, 503);
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
  ui["new-conversation"].dispatchEvent(new Event("click"));
  await until(() => /503|unavailable|fail|error/i.test(ui.status.textContent), "create error");
  assert.equal(ui["new-conversation"].disabled, false);
  assert.equal(ui.conversations.disabled, false);
  assert.equal(ui.stop.disabled, true);
  assert.match(ui.messages.textContent, /preserved saved history/);
});

test("saved history bounds are server-owned and a rejected next turn remains visible without silently trimming history", async () => {
  const saved = Array.from({ length: 16 }, (_, index) => message(index % 2 ? "assistant" : "user", `saved turn ${index}`, "completed", index + 1));
  const server = api({ initial: [conversation("first", saved)], onChat() { return json({ detail: "Conversation exceeds the 16-message history limit" }, 422); } });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "one more");
  await until(() => server.chats.length === 1 && !ui.send.disabled, "server history limit error");
  assert.deepEqual(server.chats[0].data, { content: "one more", max_tokens: 1024, temperature: 0.7 });
  assert.match(ui.status.textContent, /422|history|limit|error|fail/i);
  assert.match(ui.messages.textContent, /saved turn 0/);
  assert.match(ui.messages.textContent, /saved turn 15/);
});

test("a delayed terminal refresh cannot replace a newer active turn", async () => {
  const terminalRefresh = deferred();
  let held = false;
  let active;
  const completed = [message("user", "first prompt"), message("assistant", "first answer", "completed", 2)];
  const server = api({
    intercept({ url, method }, state) {
      if (url === "/api/conversations/first" && method === "GET" && state.chats.length === 1 && !held) {
        held = true;
        return terminalRefresh.promise;
      }
    },
    onChat(call, selected, state) {
      if (state.chats.length === 1) {
        selected.messages = structuredClone(completed);
        return success("first answer");
      }
      selected.messages = [...structuredClone(completed), message("user", call.data.content, "completed", 3), message("assistant", "new active answer", "partial", 4)];
      call.options.signal.addEventListener("abort", () => { selected.messages[3].status = "cancelled"; }, { once: true });
      active = pendingStream("new active answer");
      return active.response;
    },
  });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "first prompt");
  await until(() => held && !ui.send.disabled, "first terminal refresh pending");
  submit(ui, "second prompt");
  await until(() => ui.messages.textContent.includes("new active answer"), "new active turn");
  terminalRefresh.resolve(json(conversation("first", completed)));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.match(ui.messages.textContent, /second prompt/);
  assert.match(ui.messages.textContent, /new active answer/);
  assert.equal(ui.send.disabled, true);
  assert.equal(ui.stop.disabled, false);
  ui.stop.dispatchEvent(new Event("click"));
  await until(() => !ui.send.disabled && !active.body.locked, "new active turn cleanup");
});

test("a delayed old conversation list cannot erase a newly created selected conversation", async () => {
  const oldList = deferred();
  let held = false;
  const server = api({ intercept({ url, method }, state) {
    if (url === "/api/conversations" && method === "GET" && state.chats.length === 1 && !held) {
      held = true;
      return oldList.promise;
    }
  } });
  const ui = fixture(server.fetch);
  await ready(ui);
  submit(ui, "finish before creating another conversation");
  await until(() => held && !ui.send.disabled, "old terminal conversation list pending");
  ui["new-conversation"].dispatchEvent(new Event("click"));
  await ready(ui, "new-1");
  assert.ok(ui.conversations.children.some(({ value }) => value === "new-1"), "New conversation must appear in its selector");
  oldList.resolve(json({ items: [{ id: "first", title: "Conversation first" }], limit: 20, offset: 0 }));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.ok(ui.conversations.children.some(({ value }) => value === "new-1"), "An obsolete list must preserve the newly selected saved conversation option");
  assert.equal(ui.conversations.value, "new-1");
  submit(ui, "prompt in newly selected conversation");
  await until(() => server.chats.length === 2 && !ui.send.disabled, "new selected conversation generation");
  assert.equal(server.chats[1].url, "/api/conversations/new-1/chat");
});
