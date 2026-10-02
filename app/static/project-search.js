// Read-only project search; render source text without interpreting HTML.
(() => {
  const el = id => document.getElementById(id);
  let controller = null;
  function sourceButton(workspace, file, line, title) {
    const button = document.createElement('button');
    button.style.maxWidth = '100%'; button.style.overflowWrap = 'anywhere';
    button.textContent = title;
    button.onclick = () => document.dispatchEvent(new CustomEvent('project-source', {detail: {workspace, file, line}}));
    return button;
  }
  function clear() {
    if (controller) controller.abort();
    el('search-results').replaceChildren();
    el('search-status').textContent = '';
  }
  async function search(filesOnly, kind = null) {
    const workspace = el('workspace').value;
    const query = filesOnly ? '' : el('search-text').value.trim();
    if (!workspace) { el('search-status').textContent = 'Select a workspace first.'; return; }
    if (!filesOnly && !query) { el('search-status').textContent = 'Enter the text to find, or choose Find files.'; return; }
    clear();
    controller = new AbortController();
    const current = controller;
    el('search-status').textContent = 'Searching the selected project…';
    try {
      const params = new URLSearchParams({pattern: el('search-pattern').value.trim() || '**/*', limit: '20'});
      if (kind) { params.set('symbol', query); params.set('kind', kind); } else params.set('query', query);
      const response = await fetch(`/workspaces/${encodeURIComponent(workspace)}/${kind ? 'symbols' : 'search'}?${params}`, {signal: current.signal});
      const data = await response.json();
      if (!response.ok) throw Error(data.detail || 'Search could not complete.');
      if (el('workspace').value !== workspace || current !== controller) return;
      const results = el('search-results');
      if (data.mode === 'files') {
        const list = document.createElement('ul');
        for (const file of data.files) { const item = document.createElement('li'); item.appendChild(sourceButton(workspace, file, 1, file)); list.appendChild(item); }
        results.appendChild(list);
      } else {
        for (const match of data.matches) {
          const card = document.createElement('article');
          const heading = document.createElement('h3');
          heading.appendChild(sourceButton(workspace, match.file, match.line, `${match.file} · line ${match.line}`));
          const code = document.createElement('pre');
          code.textContent = [...match.context, {line: match.line, text: match.text}].sort((a,b) => a.line-b.line).map(x => `${x.line}: ${x.text}`).join('\n');
          card.append(heading, code); results.appendChild(card);
        }
      }
      const count = data.mode === 'files' ? data.files.length : data.matches.length;
      const label = data.mode === 'files' ? 'files' : data.mode === 'definitions' ? 'Python definitions' : data.mode === 'references' ? 'possible Python references' : 'matching lines';
      el('search-status').textContent = `${count} ${label} found in ${workspace}.${kind ? ' Syntax matches only; confirm relationships in the source.' : ''}${data.truncated ? ' Results are limited; narrow the file filter or search text.' : ''}${data.skipped_files ? ' Some unreadable, oversized, or invalid source files were skipped.' : ''}`;
    } catch (error) {
      if (error.name !== 'AbortError' && current === controller) el('search-status').textContent = error.message;
    }
  }
  el('search-submit').onclick = () => search(false);
  el('find-files').onclick = () => search(true);
  for (const [kind, label] of [['definitions', 'Find definitions'], ['references', 'Find references']]) {
    const button = document.createElement('button'); button.textContent = label;
    button.onclick = () => search(false, kind); el('search-status').before(button);
  }
  const hint = document.createElement('p'); hint.className = 'muted';
  hint.textContent = 'Definitions and references: enter one Python name in Text to find. References are possible matches, not confirmed callers.';
  el('search-status').before(hint);
  el('search-text').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); search(false); } };
  el('workspace').addEventListener('change', clear);
})();
