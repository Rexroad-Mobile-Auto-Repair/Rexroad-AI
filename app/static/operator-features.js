// Operator features reuse saved services; source and reports are rendered as text.
(() => {
  const workspace = document.getElementById('workspace');
  const scope = document.getElementById('scope');
  const make = (tag, text = '') => { const e = document.createElement(tag); e.textContent = text; return e; };
  const input = (label, max = 500) => { const e = make('input'); e.setAttribute('aria-label', label); e.maxLength = max; e.placeholder = label; e.style.maxWidth = '100%'; return e; };
  const card = title => { const e = make('section'); e.className = 'card'; e.style.overflowWrap = 'anywhere'; e.append(make('h2', title)); document.querySelector('main').append(e); return e; };
  async function api(url, options) { const r = await fetch(url, options); const d = await r.json(); if (!r.ok) throw Error(typeof d.detail === 'string' ? d.detail : 'The request could not complete.'); return d; }
  const post = body => ({method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
  const button = (label, action) => { const e = make('button', label); e.onclick = action; return e; };
  const context = () => ({workspace: workspace.value, scope: scope.value.trim()});
  const memoryScope = () => `project:${workspace.value}:${scope.value.trim()}`;
  const task = card('Task shortcuts');
  task.append(make('p', 'Read-only reviews. Enter a source file and optional context, then choose a task.'));
  const target = input('Task source file'); const note = input('Task context', 1000);
  const taskButtons = make('div'); const taskStatus = make('p'); taskStatus.setAttribute('aria-live', 'polite'); const taskReport = make('pre'); taskReport.style.overflowWrap = 'anywhere';
  task.append(target, note, taskButtons, taskStatus, taskReport);
  let taskRunning = false; let taskGeneration = 0;
  api('/operator/shortcuts').then(items => items.forEach(item => taskButtons.append(button(item.title, async () => {
    if (taskRunning) return;
    const current = ++taskGeneration, selected = context();
    taskRunning = true; taskButtons.querySelectorAll('button').forEach(b => b.disabled = true);
    taskStatus.textContent = 'Reviewing source. Files stay unchanged.'; taskReport.textContent = '';
    try { const d = await api(`/operator/shortcuts/${encodeURIComponent(item.id)}`, post({workspace: selected.workspace, target: target.value.trim(), note: note.value.trim()}));
      if (current !== taskGeneration) return;
      taskReport.textContent = d.content; taskStatus.textContent = `${(d.warnings || []).join(' ')} Read-only review; no checks were run. Saved conversation: ${d.session_id}`;
    } catch (e) { if (current === taskGeneration) taskStatus.textContent = e.message; }
    finally { taskRunning = false; taskButtons.querySelectorAll('button').forEach(b => b.disabled = false); }
  })))).catch(e => taskStatus.textContent = e.message);
  const progress = card('Worker progress'); const progressStatus = make('p'); const progressResults = make('div');
  let progressLoading = false; let progressGeneration = 0;
  async function loadProgress() {
    if (progressLoading || !workspace.value) return;
    progressLoading = true; const current = ++progressGeneration, selectedContext = context();
    try {
      const d = await api('/operator/progress?' + new URLSearchParams(selectedContext));
      if (current !== progressGeneration || selectedContext.workspace !== workspace.value || selectedContext.scope !== scope.value.trim()) return;
      progressResults.replaceChildren(); progressStatus.textContent = `${d.workspace} · ${d.scope} · ${d.workflows.length} saved coding tasks`;
      for (const w of d.workflows) {
        const box = make('article'); box.append(make('h3', w.objective.length > 100 ? w.objective.slice(0, 100) + '…' : w.objective), make('p', `Status: ${w.status} · Next: ${w.next_action.replaceAll('_', ' ')}`));
        const objective = make('details'); objective.append(make('summary', 'Task objective preview'), make('p', w.objective)); box.append(objective);
        if (w.blocked_reason && !['completed', 'cancelled'].includes(w.status)) box.append(make('p', `Blocked: ${w.blocked_reason}`));
        for (const worker of w.workers) box.append(make('p', `${worker.role}: ${worker.status}${worker.reason ? ' · ' + worker.reason : ''} · ${worker.task_id}`));
        box.append(make('p', `Proposal: ${w.proposal_status || 'Not prepared'}`));
        for (const file of w.files) box.append(make('p', `${file.path}: ${file.status}`));
        for (const check of w.checks) box.append(make('p', `${check.check}: ${check.status}`));
        progressResults.append(box);
      }
      for (const plan of d.plans) { const list = make('ol'); plan.steps.forEach(s => list.append(make('li', `${s.title}: ${s.status}`))); progressResults.append(make('p', `Plan ${plan.plan_id}: ${plan.status}`), list); }
    } catch (e) { if (current === progressGeneration) progressStatus.textContent = e.message; }
    finally { progressLoading = false; if (current !== progressGeneration || selectedContext.workspace !== workspace.value || selectedContext.scope !== scope.value.trim()) loadProgress(); }
  }
  progress.append(button('Refresh progress', loadProgress), progressStatus, progressResults);
  const memory = card('Project memory'); const memoryStatus = make('p'); const memoryResults = make('div');
  const memoryContent = input('Memory to propose', 1000); const category = make('select'); category.setAttribute('aria-label', 'Memory category');
  ['fact', 'decision', 'preference', 'note'].forEach(x => { const o = make('option', x); o.value = x; category.append(o); });
  let memoryGeneration = 0, memoryBusy = false;
  async function loadMemory() {
    if (!workspace.value) return;
    const selectedScope = memoryScope(), current = ++memoryGeneration;
    try {
      const [records, proposals] = await Promise.all([api('/memories?' + new URLSearchParams({scope: selectedScope})), api('/memory-proposals?' + new URLSearchParams({scope: selectedScope}))]);
      if (current !== memoryGeneration) return;
      memoryResults.replaceChildren(); memoryStatus.textContent = `${selectedScope} · ${records.length} active records · ${proposals.length} pending proposals`;
      records.forEach(r => memoryResults.append(make('p', `${r.category}: ${r.content}`)));
      for (const p of proposals) {
        const row = make('article'); row.append(make('p', `Proposed ${p.category}: ${p.proposed_content}`));
        for (const action of ['approve', 'reject']) row.append(button(action === 'approve' ? 'Approve memory' : 'Reject memory', async () => {
          if (memoryBusy) return; memoryBusy = true; row.querySelectorAll('button').forEach(b => b.disabled = true);
          try { await api(`/memory-proposals/${encodeURIComponent(p.id)}/${action}?` + new URLSearchParams({scope: selectedScope}), post({})); await loadMemory(); }
          catch (e) { memoryStatus.textContent = e.message; } finally { memoryBusy = false; row.querySelectorAll('button').forEach(b => b.disabled = false); }
        }));
        memoryResults.append(row);
      }
    } catch (e) { if (current === memoryGeneration) memoryStatus.textContent = e.message; }
  }
  memory.append(make('p', 'Project facts and decisions are saved only after you approve a proposal.'), memoryContent, category, button('Propose memory', async () => {
    if (memoryBusy) return; memoryBusy = true;
    try { await api('/memory-proposals', post({scope: memoryScope(), category: category.value, proposed_content: memoryContent.value.trim(), metadata: context()})); memoryContent.value = ''; await loadMemory(); }
    catch (e) { memoryStatus.textContent = e.message; } finally { memoryBusy = false; }
  }), button('Refresh memory', loadMemory), memoryStatus, memoryResults);
  const isolation = card('Isolated coding workspaces'); const isolationStatus = make('p'); const isolationResults = make('div');
  isolation.append(make('p', 'Create a separate coding workspace from the saved commit. Unsaved changes stay in the original project. Nothing is merged or pushed automatically.'));
  let isolationPreview = null, isolationBusy = false, isolationGeneration = 0;
  const createIsolation = button('Create isolated workspace', async () => {
    if (!isolationPreview || isolationBusy || isolationPreview.source_workspace !== workspace.value) return;
    isolationBusy = true; createIsolation.disabled = true; const selected = workspace.value, generation = isolationGeneration;
    isolationStatus.textContent = 'Creating a separate coding workspace…';
    try {
      const d = await api(`/workspaces/${encodeURIComponent(selected)}/isolate`, post({base_commit: isolationPreview.base_commit}));
      if (generation === isolationGeneration) isolationStatus.textContent = `Created ${d.workspace} from ${d.base_commit.slice(0, 12)}. Original files are unchanged.`;
      const option = make('option', d.workspace); option.value = d.workspace; workspace.append(option);
      isolationPreview = null; await loadIsolation();
    } catch (e) { if (generation === isolationGeneration) isolationStatus.textContent = e.message; }
    finally { isolationBusy = false; }
  }); createIsolation.disabled = true;
  async function loadIsolation() {
    const selected = workspace.value, generation = isolationGeneration;
    try {
      const records = await api('/isolated-workspaces');
      if (generation !== isolationGeneration || selected !== workspace.value) return;
      isolationResults.replaceChildren();
      for (const r of records.filter(r => r.source_workspace === selected || r.workspace === selected)) {
        const row = make('article'); row.append(make('p', `${r.workspace} · ${r.branch} · ${r.status}`));
        if (r.available) row.append(button('Use ' + r.workspace, () => {
          if (!Array.from(workspace.options).some(o => o.value === r.workspace)) { const o = make('option', r.workspace); o.value = r.workspace; workspace.append(o); }
          workspace.value = r.workspace; workspace.dispatchEvent(new Event('change'));
        }));
        isolationResults.append(row);
      }
    } catch (e) { if (generation === isolationGeneration) isolationStatus.textContent = e.message; }
  }
  isolation.append(button('Preview isolation', async () => {
    if (isolationBusy) return; const selected = workspace.value, generation = ++isolationGeneration;
    isolationPreview = null; createIsolation.disabled = true;
    try {
      const d = await api(`/workspaces/${encodeURIComponent(selected)}/isolation-preview`);
      if (generation !== isolationGeneration || workspace.value !== selected) return;
      isolationPreview = d; createIsolation.disabled = false;
      isolationStatus.textContent = `Saved commit: ${d.base_commit.slice(0, 12)} · ${d.branch || 'Detached commit'}. ${d.has_unsaved_changes ? 'This project has unsaved changes; they will NOT be copied.' : 'No unsaved changes.'}`;
    } catch (e) { if (generation === isolationGeneration) isolationStatus.textContent = e.message; }
  }), createIsolation, button('Refresh isolated workspaces', loadIsolation), isolationStatus, isolationResults);
  const integrations = card('External integrations'); const integrationStatus = make('p'); const integrationResults = make('div'); let integrationBusy = false;
  integrations.append(make('p', 'Inspect and reconnect trusted, preconfigured tool servers. Only their read-only tools are exposed to the AI.'));
  async function loadIntegrations() {
    try {
      const items = await api('/operator/integrations'); integrationResults.replaceChildren();
      integrationStatus.textContent = items.length ? `${items.length} configured integrations` : 'No external tool servers are configured.';
      for (const item of items) {
        const row = make('article'); row.append(make('h3', item.name), make('p', `${item.connected ? 'Connected' : 'Disconnected'} · ${item.tool_count} tools · ${item.resource_count} resources`));
        if (item.last_error) row.append(make('p', item.last_error));
        item.tools.forEach(t => row.append(make('p', `${t.name}: ${t.enabled ? 'Read-only tool available' : 'Not exposed'}`)));
        row.append(button(item.connected ? 'Disconnect ' + item.name : 'Connect ' + item.name, async () => {
          if (integrationBusy) return; integrationBusy = true; row.querySelectorAll('button').forEach(b => b.disabled = true);
          try { await api(`/operator/integrations/${encodeURIComponent(item.name)}/action`, post({action: item.connected ? 'disconnect' : 'connect'})); await loadIntegrations(); }
          catch (e) { integrationStatus.textContent = e.message; } finally { integrationBusy = false; }
        })); integrationResults.append(row);
      }
    } catch (e) { integrationStatus.textContent = e.message; }
  }
  integrations.append(button('Refresh integrations', loadIntegrations), integrationStatus, integrationResults);
  function changed() { taskGeneration++; taskReport.textContent = ''; taskStatus.textContent = ''; progressGeneration++; progressResults.replaceChildren(); progressStatus.textContent = 'Loading saved progress…'; memoryGeneration++; memoryResults.replaceChildren(); memoryStatus.textContent = 'Loading project memory…'; isolationGeneration++; isolationPreview = null; createIsolation.disabled = true; isolationStatus.textContent = ''; isolationResults.replaceChildren(); loadProgress(); loadMemory(); loadIsolation(); }
  workspace.addEventListener('change', changed); scope.addEventListener('change', changed);
  document.getElementById('refresh').addEventListener('click', () => { loadProgress(); loadMemory(); });
  document.addEventListener('operator-context-ready', changed);
  setInterval(() => { if (!document.hidden) loadProgress(); }, 10000);
  loadProgress(); loadMemory(); loadIsolation(); loadIntegrations();
})();
