// Reuse the persisted coding-job services; each click performs one action.
const $ = id => document.getElementById(id);
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let selected = null;
const params = () => new URLSearchParams({scope: $('scope').value.trim(), workspace: $('workspace').value}).toString();
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || 'The request could not be completed. Saved progress is preserved.');
  return data;
}
async function load() {
  if (!$('workspace').value) return;
  try {
    const d = await api('/supervisor/dashboard?' + params());
    $('project').textContent = `${d.project.workspace} · ${d.project.branch || 'No branch'} · ${d.project.clean ? 'Clean' : 'Has local changes'}`;
    $('attention').innerHTML = (d.attention.map(x => `<p>${safe(x.headline)} — ${safe(x.reason)}</p>`).join('') || 'Nothing requires attention.') + ((d.history || []).length ? `<details><summary>Task history</summary>${d.history.map(x => `<p>${safe(x.headline)} — ${safe(x.reason)}</p>`).join('')}</details>` : '');
    $('jobs').innerHTML = d.coding.map(x => `<p>${safe(x.status)} <button data-job="${safe(x.workflow_id)}">Open guidance</button></p>`).join('') || 'No coding jobs.';
    $('activity').innerHTML = d.recent_activity.map(x => `<p>${safe(x.timestamp)} · ${safe(x.summary)}</p>`).join('') || 'No recent activity.';
    document.querySelectorAll('[data-job]').forEach(b => b.onclick = () => selectJob(b.dataset.job));
    $('error').textContent = '';
  } catch (e) { $('error').textContent = e.message; }
}
async function loadWorkspaces() {
  try {
    const workspaces = await api('/workspaces');
    $('workspace').innerHTML = workspaces.filter(x => x.available).map(x => `<option value="${safe(x.name)}">${safe(x.name)}</option>`).join('');
    await load();
  } catch (e) { $('error').textContent = e.message; }
}
let operatorBusy = false;
const actionLabels = {
  start_analysis: 'Analyze the task', review_analysis: 'Review analysis',
  create_proposal: 'Generate proposed changes', review_proposal: 'Review proposed changes',
  convert_proposal: 'Prepare reviewed changes', review_specs: 'Review execution steps',
  request_patch_approval: 'Request file approval', review_patch_approvals: 'Approve file changes',
  execute_patches: 'Apply approved changes', execute_checks: 'Run checks',
  start_verifier: 'Verify the result', review_verifier: 'Review verification',
  request_revision: 'Request a revision', retry_checks: 'Retry checks without reapplying files',
  retry_verifier: 'Retry read-only verification'
};
async function selectJob(id) {
  selected = id;
  try {
    const p = params(), j = await api(`/supervisor-coding-workflows/${encodeURIComponent(id)}/job?${p}`);
    const g = await api(`/supervisor-coding-workflows/${encodeURIComponent(id)}/guidance?${p}`);
    let report = '', diff = '', retryable = false;
    const taskId = g.next_action === 'review_verifier' || j.status === 'completed' ? j.verifier_task_id : j.analyst_task_id;
    if (taskId) {
      const task = await api(`/sub-agent-tasks/${encodeURIComponent(taskId)}`);
      const reason = task.result?.safe_reason;
      const summary = task.result?.summary || (reason === 'provider_error' ? 'The model service could not complete this request. It may be offline or have timed out. Start the local model and retry. This failed attempt remains saved.' : 'No report available.');
      retryable = j.status === 'failed' && !j.execution_trace_ids.length && reason === 'provider_error';
      report = `<details open><summary>Saved worker report</summary><pre>${safe(summary)}</pre></details>`;
    }
    if (j.proposal_preview_available) {
      const preview = await api(`/supervisor-coding-workflows/${encodeURIComponent(id)}/proposal/preview?${p}`);
      diff = `<h3>Proposed changes</h3>${(preview.changes || []).map(change => `<h4>${safe(change.relative_path)}</h4><pre>${safe(change.unified_diff)}</pre>${change.truncated ? '<p>Preview shortened. Review the full proposal before applying changes.</p>' : ''}`).join('')}`;
    }
    const label = actionLabels[g.next_action] || (j.status === 'completed' ? 'Verified' : 'Stopped');
    const review = g.next_action.startsWith('review_');
    $('jobs').innerHTML = `<div class="attention"><h3>${safe(j.status)}</h3><p>${safe(g.explanation)}</p>${report}${diff}${g.next_action === 'retry_verifier' ? '<label>Verification context<textarea id="verification-note" aria-label="Verification context" maxlength="2000" rows="4" placeholder="Add current source evidence or explain a disputed finding"></textarea></label>' : ''}${g.next_action === 'request_revision' ? '<label>Revision instructions<textarea id="revision-note" aria-label="Revision instructions" maxlength="2000" rows="4" placeholder="Describe the required corrections"></textarea></label>' : ''}${(j.verification_conflicts || []).map(x => `<p class="warning">${safe(x)}</p>`).join('')}<p>Files: ${g.affected_resources.map(safe).join(', ') || 'No file changes prepared.'}</p><p>Checks: ${j.check_specs.map(x => safe(x.check_id) + ': ' + safe(x.execution_status)).join(', ') || 'Not prepared.'}</p><button id="action" ${!g.action_available || operatorBusy || (g.next_action === 'review_verifier' && j.verification_conflicts?.length) ? 'disabled' : ''}>${review ? 'Accept' : safe(label)}</button>${review ? '<button id="reject">Reject</button>' : ''}</div>`;
    $('action').onclick = () => runAction(g.next_action, review ? 'accept' : null, g);
    if ($('reject')) $('reject').onclick = () => runAction(g.next_action, 'reject', g);
    if (retryable) {
      const retry = document.createElement('button');
      retry.textContent = 'Retry analysis as a new task';
      retry.onclick = () => {
        $('objective').value = j.objective + ' Read only the named source files; skip Git and knowledge tools. Keep the final analysis under 150 words; do not output full files.';
        $('create').click();
      };
      $('jobs').appendChild(retry);
    }
  } catch (e) { $('error').textContent = e.message; }
}
async function runAction(action, decision, guidance) {
  if (!selected || operatorBusy) return;
  const id = selected, scope = $('scope').value.trim(), body = {action, scope};
  if (decision) body.decision = decision;
  if (action === 'review_patch_approvals') {
    body.approval_request_id = guidance.action_preview.pending_request_ids[0];
    if (!body.approval_request_id) { $('error').textContent = 'No pending file approval was found. Refresh the task.'; return; }
  }
  if (action === 'retry_verifier') body.note = $('verification-note').value.trim() || null;
  if (action === 'request_revision') { body.note = $('revision-note').value.trim(); if (!body.note) { $('error').textContent = 'Describe the required corrections first.'; return; } }
  operatorBusy = true;
  $('jobs').querySelectorAll('button').forEach(b => b.disabled = true);
  $('error').textContent = 'Working. Your saved progress remains available.';
  let failure = '';
  try {
    const generate = action === 'create_proposal' || action === 'request_revision';
    const endpoint = generate ? `/supervisor-coding-workflows/${encodeURIComponent(id)}/proposal/generate?scope=${encodeURIComponent(scope)}` : `/supervisor-coding-workflows/${encodeURIComponent(id)}/action`;
    await api(endpoint, {method: 'POST', headers: {'content-type': 'application/json'}, body: generate ? JSON.stringify({note: body.note || null}) : JSON.stringify(body)});
  } catch (e) { failure = e.message; }
  finally { operatorBusy = false; await load(); await selectJob(id); $('error').textContent = failure; }
}
$('scope').onchange = () => { selected = null; load(); };
$('workspace').onchange = () => { selected = null; load(); };
$('refresh').onclick = load;
$('create').onclick = async () => {
  if (operatorBusy) return;
  const instruction = $('objective').value.trim();
  if (!instruction) { $('error').textContent = 'Describe the task first.'; return; }
  operatorBusy = true;
  $('create').disabled = true;
  try {
    const workflow = await api('/supervisor-workflows/coding', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({scope:$('scope').value.trim(), workspace:$('workspace').value, instruction})});
    $('objective').value = '';
    await load();
    await selectJob(workflow.workflow_id);
  } catch (e) { $('error').textContent = e.message; }
  finally { operatorBusy = false; $('create').disabled = false; if (selected) await selectJob(selected); }
};
loadWorkspaces();
