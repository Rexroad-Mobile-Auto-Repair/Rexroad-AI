// Read-only source viewer. Source is always rendered as text.
(() => {
  const workspace = document.getElementById('workspace');
  const panel = document.createElement('section');
  panel.className = 'card'; panel.hidden = true;
  const title = document.createElement('h2');
  const status = document.createElement('p'); status.setAttribute('aria-live', 'polite');
  const previous = document.createElement('button'); previous.textContent = 'Previous lines';
  const next = document.createElement('button'); next.textContent = 'Next lines';
  const close = document.createElement('button'); close.textContent = 'Close file';
  const source = document.createElement('pre'); source.setAttribute('aria-label', 'Source file');
  source.style.maxHeight = '26rem'; source.style.overflow = 'auto';
  title.style.overflowWrap = 'anywhere';
  panel.append(title, status, previous, next, close, source);
  document.getElementById('search-results').parentElement.after(panel);
  let controller = null;
  function clear() {
    if (controller) controller.abort();
    controller = null; panel.hidden = true; source.replaceChildren();
  }
  async function open(detail) {
    if (detail.workspace !== workspace.value) return;
    clear(); controller = new AbortController(); const current = controller;
    panel.hidden = false; title.textContent = detail.file;
    status.textContent = 'Loading source…'; previous.disabled = next.disabled = true;
    try {
      const params = new URLSearchParams({path: detail.file, line: String(detail.line)});
      const response = await fetch(`/workspaces/${encodeURIComponent(detail.workspace)}/source?${params}`, {signal: current.signal});
      const data = await response.json();
      if (current !== controller || workspace.value !== detail.workspace) return;
      if (!response.ok) throw Error(data.detail || 'Could not open source.');
      status.textContent = `${data.workspace} · Lines ${data.total_lines ? data.start_line : 0}–${data.end_line} of ${data.total_lines} · Read only`;
      let selected = null;
      for (const row of data.lines) {
        const span = document.createElement('span');
        span.style.display = 'block'; span.style.overflowWrap = 'anywhere';
        span.textContent = `${row.line}: ${row.text}`;
        if (row.line === data.selected_line) {
          span.style.background = '#28465c'; span.setAttribute('aria-label', `Selected line ${row.line}`); selected = span;
        }
        source.appendChild(span);
      }
      previous.disabled = data.start_line <= 1;
      next.disabled = data.end_line >= data.total_lines;
      previous.onclick = () => open({...detail, line: Math.max(1, data.start_line - 80)});
      next.onclick = () => open({...detail, line: data.end_line + 1});
      (selected || panel).scrollIntoView({block: 'center'});
    } catch (error) {
      if (error.name !== 'AbortError' && current === controller) status.textContent = error.message;
    }
  }
  document.addEventListener('project-source', event => open(event.detail));
  workspace.addEventListener('change', clear); close.onclick = clear;
})();
