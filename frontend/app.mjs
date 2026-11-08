import {consumeSSE} from './stream.mjs';

export function mountChat(document, fetchImpl = globalThis.fetch) {
  const form = document.getElementById('chat-form');
  const prompt = document.getElementById('prompt');
  const messages = document.getElementById('messages');
  const status = document.getElementById('status');
  const send = document.getElementById('send');
  const stop = document.getElementById('stop');
  const maxTokens = document.getElementById('max-tokens');
  const temperature = document.getElementById('temperature');
  const history = [];
  let active = null;

  function busy(value) {
    send.disabled = value;
    stop.disabled = !value;
    prompt.disabled = value;
  }
  function bubble(role, text) {
    const article = document.createElement('article');
    article.setAttribute('class', `message ${role}`);
    const label = document.createElement('strong');
    label.textContent = role === 'user' ? 'You' : 'Llama';
    const content = document.createElement('pre');
    content.textContent = text;
    article.append(label, content);
    messages.append(article);
    return content;
  }
  busy(false);
  stop.addEventListener('click', () => {
    if (!active) return;
    active.controller.abort();
    active = null;
    status.textContent = 'Stopped. Partial output is kept above.';
    busy(false);
    prompt.focus();
  });
  prompt.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!active) form.requestSubmit();
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (active) return;
    const text = prompt.value.trim();
    if (!text) { status.textContent = 'Enter a message first.'; return; }
    if (text.length > 4000) { status.textContent = 'Keep messages within 4,000 characters.'; return; }
    const requestMessages = [...history, {role: 'user', content: text}];
    if (requestMessages.length > 16 || requestMessages.some(m => m.content.length > 4000) ||
        requestMessages.reduce((n, m) => n + m.content.length, 0) > 8000) {
      status.textContent = 'Conversation limit reached. Refresh to start a new chat.';
      return;
    }
    const tokens = Number(maxTokens.value), heat = Number(temperature.value);
    if (!Number.isInteger(tokens) || tokens < 1 || tokens > 2048 ||
        temperature.value.trim() === '' || !Number.isFinite(heat) || heat < 0 || heat > 2) {
      status.textContent = 'Use 1–2048 output tokens and a temperature from 0 to 2.';
      return;
    }
    const run = {controller: new AbortController()};
    active = run;
    busy(true);
    bubble('user', text);
    const answer = bubble('assistant', '');
    prompt.value = '';
    status.textContent = 'Generating…';
    try {
      const response = await fetchImpl('/api/chat', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: run.controller.signal,
        body: JSON.stringify({messages: requestMessages, max_tokens: tokens, temperature: heat}),
      });
      if (run.controller.signal.aborted) {
        await response.body?.cancel();
        throw new DOMException('Stopped', 'AbortError');
      }
      if (!response.ok) {
        await response.body?.cancel();
        throw new Error(`Request rejected (HTTP ${response.status}). Try a shorter prompt.`);
      }
      if (!(response.headers.get('content-type') || '').startsWith('text/event-stream')) {
        await response.body?.cancel();
        throw new Error('The server did not return an event stream.');
      }
      const output = await consumeSSE(response.body, delta => {
        if (active === run) {
          answer.textContent += delta;
          messages.scrollTop = messages.scrollHeight;
        }
      }, run.controller.signal);
      if (!output) throw new Error('The model returned no text. Try another prompt.');
      if (active === run) {
        history.push({role: 'user', content: text}, {role: 'assistant', content: output});
        status.textContent = 'Ready.';
      }
    } catch (error) {
      if (active === run) status.textContent = error.name === 'AbortError' ? 'Stopped.' : `Error: ${error.message}`;
    } finally {
      if (active === run) { active = null; busy(false); prompt.focus(); }
    }
  });
}

if (typeof document !== 'undefined') mountChat(document);
