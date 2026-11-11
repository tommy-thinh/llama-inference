// Fetch chunks and SSE events have independent boundaries.
export async function consumeSSE(body, onText, signal) {
  if (!body) throw new Error('The response has no stream.');
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let buffer = '', data = [], event = '', size = 0, output = '', completed = false;
  const abort = () => { reader.cancel().catch(() => {}); };
  signal?.addEventListener('abort', abort, {once: true});
  try {
    if (signal?.aborted) throw new DOMException('Stopped', 'AbortError');
    while (!completed) {
      const chunk = await reader.read();
      if (signal?.aborted) throw new DOMException('Stopped', 'AbortError');
      buffer += decoder.decode(chunk.value, {stream: !chunk.done});
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).replace(/\r$/, '');
        buffer = buffer.slice(end + 1);
        if (line === '') {
          if (data.length) {
            const text = data.join('\n');
            if (text === '[DONE]') { completed = true; break; }
            let packet;
            try { packet = JSON.parse(text); }
            catch { throw new Error('The server returned an invalid stream event.'); }
            if (!packet || typeof packet !== 'object' || Array.isArray(packet)) {
              throw new Error('The server returned an invalid stream event.');
            }
            if (event === 'error' || packet.error) {
              throw new Error(packet.error?.message || packet.message || 'Generation failed.');
            }
            const delta = packet.choices?.[0]?.delta?.content;
            if (delta != null && typeof delta !== 'string') throw new Error('Invalid text delta.');
            if (delta) {
              if (output.length + delta.length > 65536) throw new Error('Response exceeds the display limit.');
              output += delta;
              onText(delta);
            }
          }
          data = []; event = ''; size = 0;
        } else {
          size += line.length + 1;
          if (size > 65536) throw new Error('Stream event exceeds the limit.');
          if (line.startsWith(':')) continue;
          const colon = line.indexOf(':');
          const field = colon < 0 ? line : line.slice(0, colon);
          const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
          if (field === 'data') data.push(value);
          if (field === 'event') event = value;
        }
      }
      if (buffer.length + size > 65536) throw new Error('Stream event exceeds the limit.');
      if (chunk.done && !completed) throw new Error('The stream ended before completion.');
    }
    return output;
  } finally {
    signal?.removeEventListener('abort', abort);
    try { await reader.cancel(); } catch { /* Preserve the original stream failure. */ }
    reader.releaseLock();
  }
}
