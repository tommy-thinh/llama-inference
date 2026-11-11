import {consumeSSE} from './stream.mjs';

export async function mountChat(document, fetchImpl = globalThis.fetch) {
  const form = document.getElementById('chat-form');
  const prompt = document.getElementById('prompt');
  const messages = document.getElementById('messages');
  const status = document.getElementById('status');
  const send = document.getElementById('send');
  const stop = document.getElementById('stop');
  const maxTokens = document.getElementById('max-tokens');
  const temperature = document.getElementById('temperature');
  const conversations = document.getElementById('conversations');
  const newConversation = document.getElementById('new-conversation');
  let selected = null, active = null, loading = true, version = 0;

  function controls() {
    send.disabled = loading || !!active || !selected;
    stop.disabled = !active;
    prompt.disabled = loading || !!active;
    newConversation.disabled = loading || !!active;
    conversations.disabled = !!active;
  }
  async function api(path, options = {}) {
    const response = await fetchImpl(path, {
      credentials: 'same-origin', ...options,
      headers: {'Content-Type': 'application/json', ...options.headers},
    });
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).detail; } catch { /* Safe generic fallback. */ }
      throw new Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status}).`);
    }
    return response.json();
  }
  function bubble(role, text, state = 'completed') {
    const article = document.createElement('article');
    article.setAttribute('class', `message ${role}`);
    article.setAttribute('data-status', state);
    const label = document.createElement('strong');
    label.textContent = (role === 'user' ? 'You' : 'Llama') + (state === 'completed' ? '' : ` · ${state}`);
    const content = document.createElement('pre');
    content.textContent = text;
    article.append(label, content);
    messages.append(article);
    return content;
  }
  function render(detail) {
    messages.replaceChildren();
    for (const message of detail.messages || []) bubble(message.role, message.content, message.status);
  }
  async function list(epoch = version) {
    const data = await api('/api/conversations');
    if (epoch !== version) return data.items;
    conversations.replaceChildren();
    for (const conversation of data.items) {
      const option = document.createElement('option');
      option.value = conversation.id;
      option.textContent = conversation.title;
      conversations.append(option);
    }
    if (selected) conversations.value = selected;
    return data.items;
  }
  async function load(id, epoch = ++version) {
    conversations.value = id;
    loading = true; controls();
    try {
      const detail = await api(`/api/conversations/${id}`);
      if (epoch !== version) return;
      selected = id;
      render(detail);
      status.textContent = detail.active_request ? 'A saved generation is still finishing. Retry shortly.' : 'Ready.';
    } catch (error) {
      if (epoch === version) {
        conversations.value = selected || '';
        status.textContent = `Error: ${error.message}`;
      }
    } finally {
      if (epoch === version) { loading = false; controls(); }
    }
  }
  async function refresh(run) {
    for (const delay of [0, 100, 250]) {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (active || selected !== run.id || version !== run.version) return;
      try {
        const detail = await api(`/api/conversations/${run.id}`);
        if (active || selected !== run.id || version !== run.version) return;
        if (detail.active_request) continue;
        render(detail);
        await list();
        return;
      } catch { return; } // Keep visible text when saved-state refresh is unavailable.
    }
  }
  controls();
  conversations.addEventListener('change', () => {
    if (!active && conversations.value) void load(conversations.value);
  });
  newConversation.addEventListener('click', async () => {
    if (active || loading) return;
    const epoch = ++version;
    loading = true; controls();
    try {
      const created = await api('/api/conversations', {method: 'POST', body: JSON.stringify({title: 'New conversation'})});
      if (epoch !== version) return;
      await list();
      if (epoch === version) await load(created.id, epoch);
    } catch (error) {
      if (epoch === version) { status.textContent = `Error: ${error.message}`; loading = false; controls(); }
    }
  });
  stop.addEventListener('click', () => {
    if (!active) return;
    const stopped = active;
    stopped.controller.abort(); active = null;
    status.textContent = 'Stopped. Partial output is kept and saved.';
    controls(); prompt.focus();
    void refresh(stopped);
  });
  prompt.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!active && !loading) form.requestSubmit();
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (active || loading || !selected) return;
    const text = prompt.value.trim();
    if (!text) { status.textContent = 'Enter a message first.'; return; }
    if (text.length > 4000) { status.textContent = 'Keep messages within 4,000 characters.'; return; }
    const tokens = Number(maxTokens.value), heat = Number(temperature.value);
    if (!Number.isInteger(tokens) || tokens < 1 || tokens > 2048 ||
        temperature.value.trim() === '' || !Number.isFinite(heat) || heat < 0 || heat > 2) {
      status.textContent = 'Use 1–2048 output tokens and a temperature from 0 to 2.'; return;
    }
    const run = {controller: new AbortController(), id: selected, version};
    active = run; controls();
    bubble('user', text);
    const answer = bubble('assistant', '', 'partial');
    prompt.value = ''; status.textContent = 'Generating…';
    try {
      const response = await fetchImpl(`/api/conversations/${run.id}/chat`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin',
        signal: run.controller.signal,
        body: JSON.stringify({content: text, max_tokens: tokens, temperature: heat}),
      });
      if (run.controller.signal.aborted) {
        await response.body?.cancel(); throw new DOMException('Stopped', 'AbortError');
      }
      if (!response.ok) {
        let detail;
        try { detail = (await response.json()).detail; } catch { /* Generic status below. */ }
        try { await response.body?.cancel(); } catch { /* Body may already be consumed. */ }
        throw new Error(typeof detail === 'string' ? detail : `Request rejected (HTTP ${response.status}).`);
      }
      if (!(response.headers.get('content-type') || '').startsWith('text/event-stream')) {
        await response.body?.cancel(); throw new Error('The server did not return an event stream.');
      }
      const output = await consumeSSE(response.body, delta => {
        if (active === run) { answer.textContent += delta; messages.scrollTop = messages.scrollHeight; }
      }, run.controller.signal);
      if (!output) throw new Error('The model returned no text. Try another prompt.');
      if (active === run) status.textContent = 'Ready.';
    } catch (error) {
      if (active === run) status.textContent = error.name === 'AbortError' ? 'Stopped.' : `Error: ${error.message}`;
    } finally {
      if (active === run) { active = null; controls(); prompt.focus(); void refresh(run); }
    }
  });
  const epoch = ++version;
  try {
    await api('/api/session', {method: 'POST'});
    const items = await list();
    if (epoch !== version) return;
    let identity = items[0]?.id;
    if (!identity) identity = (await api('/api/conversations', {method: 'POST', body: JSON.stringify({title: 'New conversation'})})).id;
    if (epoch !== version) return;
    await list();
    if (epoch === version) await load(identity, epoch);
  } catch (error) {
    if (epoch === version) { status.textContent = `Error: ${error.message} Refresh to retry.`; loading = false; controls(); }
  }
}

if (typeof document !== 'undefined') void mountChat(document);
