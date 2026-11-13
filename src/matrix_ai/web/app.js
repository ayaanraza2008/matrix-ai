'use strict';

/* MATRIX dashboard client.
   All state comes from the authenticated local API. Nothing is simulated: when a
   capability is unavailable the server returns an error and the UI says so. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const TERMINAL = new Set(['COMPLETE', 'PARTIAL', 'FAILED', 'CANCELLED']);
const STATE_KIND = {
  COMPLETE: 'ok',
  PARTIAL: 'warn',
  FAILED: 'bad',
  CANCELLED: 'bad',
  WAITING_FOR_USER: 'warn',
  APPROVAL_REQUIRED: 'warn',
  EXECUTE: 'live',
  VERIFY: 'live',
  RECEIVED: 'live',
  UNDERSTAND: 'live',
  POLICY_CHECK: 'live',
  PLAN: 'live',
  TOOL_SELECTION: 'live',
  RETRY_SAFE: 'live',
};

const STATE = {
  token: localStorage.getItem('matrix_token') || '',
  view: 'dashboard',
  health: null,
  status: null,
  tasks: [],
  selectedTaskId: null,
  settings: null,
  models: null,
  policy: null,
  tools: [],
  agents: [],
  integrations: [],
  devices: [],
  emergency: false,
  coreState: 'IDLE',
  booted: false,
};

const VIEW_META = {
  dashboard: ['Dashboard', 'System state, active work and quick actions'],
  chat: ['Chat', 'Conversation with the local core'],
  tasks: ['Tasks', 'Plans, tool traces, approvals and evidence'],
  memory: ['Memory', 'Governed, inspectable, deletable memory'],
  voice: ['Voice', 'Microphone session state and speech controls'],
  vision: ['Vision', 'Permission-gated capture sessions and OCR'],
  agents: ['Agents', 'Logical roles sharing one model runtime'],
  tools: ['Tools', 'Typed registry with risk, scope and verification'],
  devices: ['Devices', 'Paired companions and revocation'],
  integrations: ['Integrations', 'Optional adapters with honest health states'],
  security: ['Security', 'Policy, emergency stop and audit trail'],
  settings: ['Settings', 'Runtime configuration and resource budget'],
};

class ApiError extends Error {
  constructor(code, message, status, detail) {
    super(message || code || 'Request failed');
    this.code = code;
    this.status = status;
    this.detail = detail || null;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, { method = 'GET', body = null } = {}) {
  const headers = {};
  if (body !== null) headers['Content-Type'] = 'application/json';
  if (STATE.token) headers.Authorization = `Bearer ${STATE.token}`;
  let response;
  try {
    response = await fetch('/api' + path, {
      method,
      headers,
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    setLink('offline');
    throw new ApiError('network_error', 'Cannot reach the local MATRIX service');
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (response.status === 401) {
    lockDashboard('Session expired. Paste the owner token again.');
    throw new ApiError('unauthenticated', 'Authentication required', 401);
  }
  if (!response.ok) {
    throw new ApiError(
      (data && data.code) || 'http_' + response.status,
      (data && data.message) || response.statusText,
      response.status,
      data && data.detail,
    );
  }
  return data;
}

function setLink(kind) {
  const node = $('#link-status');
  if (!node) return;
  const map = {
    linked: ['ok', 'linked'],
    connecting: ['', 'connecting'],
    offline: ['bad', 'offline'],
    degraded: ['warn', 'degraded'],
  };
  const [cls, label] = map[kind] || map.connecting;
  node.className = 'pill ' + cls;
  node.textContent = label;
}

function toast(message, kind = 'info') {
  const node = document.createElement('div');
  node.className = 'toast ' + kind;
  node.textContent = message;
  $('#toasts').appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pill(text, kind = '') {
  return el('span', 'pill ' + kind, text);
}

function riskChip(risk) {
  return el('span', 'risk ' + String(risk || 'low'), String(risk || 'low'));
}

function shortId(id) {
  return id ? id.slice(0, 8) : '—';
}

function fmtTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (error) {
    return String(value);
  }
}

function truncate(text, limit = 160) {
  const value = String(text || '');
  return value.length > limit ? value.slice(0, limit) + '…' : value;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function statePill(state) {
  return pill(state, STATE_KIND[state] || '');
}

/* ------------------------------------------------------------------ */
/* login / session                                                     */
/* ------------------------------------------------------------------ */
function showLogin(message) {
  $('#login').classList.remove('hidden');
  $('#app').classList.add('hidden');
  if (message) $('#login-hint').textContent = message;
  const input = $('#token-input');
  input.value = '';
  setTimeout(() => input.focus(), 30);
}

function hideLogin() {
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

function lockDashboard(message) {
  STATE.token = '';
  localStorage.removeItem('matrix_token');
  showLogin(message || 'Dashboard locked. Paste the owner token to reconnect.');
}

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = $('#token-input').value.trim();
  if (!token) return;
  STATE.token = token;
  try {
    await api('/auth/me');
    localStorage.setItem('matrix_token', token);
    hideLogin();
    await boot();
    toast('Connected to MATRIX', 'ok');
  } catch (error) {
    STATE.token = '';
    toast(error.message, 'error');
  }
});

$('#lock-btn').addEventListener('click', () => lockDashboard('Dashboard locked.'));

/* ------------------------------------------------------------------ */
/* navigation                                                          */
/* ------------------------------------------------------------------ */
function switchView(view) {
  STATE.view = view;
  $$('#nav button').forEach((button) => {
    button.classList.toggle('active', button.dataset.nav === view);
  });
  $$('.view').forEach((section) => {
    section.classList.toggle('hidden', section.dataset.view !== view);
  });
  const [title, subtitle] = VIEW_META[view] || ['MATRIX', ''];
  $('#view-title').textContent = title;
  $('#view-subtitle').textContent = subtitle;
  loadView(view);
}

$$('#nav button').forEach((button) => {
  button.addEventListener('click', () => switchView(button.dataset.nav));
});

function loadView(view) {
  const loaders = {
    dashboard: loadDashboard,
    tasks: loadTasks,
    memory: () => loadMemory(),
    voice: loadVoice,
    vision: loadVision,
    agents: loadAgents,
    tools: loadTools,
    devices: loadDevices,
    integrations: loadIntegrations,
    security: loadSecurity,
    settings: loadSettings,
  };
  if (loaders[view]) loaders[view]().catch((error) => toast(error.message, 'error'));
}

/* ------------------------------------------------------------------ */
/* dashboard                                                           */
/* ------------------------------------------------------------------ */
async function loadNotifications(unreadOnly = false) {
  const result = await api('/notifications?limit=50&unread_only=' + String(unreadOnly));
  $('#notification-unread').textContent = String(result.unread_count || 0) + ' unread';
  const container = $('#notification-list');
  clearNode(container);
  const items = result.notifications || [];
  if (!items.length) {
    container.appendChild(el('div', 'empty', unreadOnly ? 'No unread notifications.' : 'No notifications yet.'));
    return;
  }
  items.forEach((notification) => {
    const item = el('div', 'item notification-item' + (notification.read ? ' is-read' : ''));
    const main = el('div', 'item-main');
    const heading = el('div', 'item-title');
    heading.textContent = notification.title || 'MATRIX notification';
    const severity = String(notification.severity || 'info');
    heading.appendChild(pill(severity, severity === 'critical' || severity === 'warning' ? 'warn' : severity === 'success' ? 'ok' : ''));
    main.appendChild(heading);
    main.appendChild(el('div', 'item-sub', notification.message || ''));
    main.appendChild(el('div', 'item-sub', (notification.event_type || 'system') + ' · ' + fmtDate(notification.created_at)));
    item.appendChild(main);
    if (!notification.read) {
      const read = el('button', 'ghost small', 'Mark read');
      read.addEventListener('click', async () => {
        read.disabled = true;
        try {
          await api('/notifications/' + encodeURIComponent(notification.id) + '/read', { method: 'POST', body: {} });
          await loadNotifications($('#notifications-filter').dataset.unread === 'true');
        } catch (error) {
          toast(error.message, 'error');
          read.disabled = false;
        }
      });
      const actions = el('div', 'item-actions');
      actions.appendChild(read);
      item.appendChild(actions);
    }
    container.appendChild(item);
  });
}

async function loadNotificationPreferences() {
  const preferences = await api('/notifications/preferences');
  $('#pref-health-recovered').checked = Boolean(preferences.health_recovered);
  $('#pref-daily-digest').checked = Boolean(preferences.daily_digest);
}

$('#notifications-refresh').addEventListener('click', () => {
  loadNotifications($('#notifications-filter').dataset.unread === 'true').catch((error) => toast(error.message, 'error'));
});
$('#notifications-filter').addEventListener('click', () => {
  const button = $('#notifications-filter');
  const unread = button.dataset.unread !== 'true';
  button.dataset.unread = String(unread);
  button.textContent = unread ? 'Show all' : 'Unread only';
  loadNotifications(unread).catch((error) => toast(error.message, 'error'));
});
$('#notification-preferences-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/notifications/preferences', {
      method: 'PUT',
      body: {
        health_recovered: $('#pref-health-recovered').checked,
        daily_digest: $('#pref-daily-digest').checked,
      },
    });
    toast('Notification preferences saved', 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
});

async function loadDashboard() {
  try {
    const [health, status] = await Promise.all([api('/health'), api('/status')]);
    STATE.health = health;
    STATE.status = status;
    STATE.emergency = Boolean(status.emergency_stop);
    setLink(health.status === 'ok' ? 'linked' : 'degraded');
    renderHealthCards();
    renderRecentTasks(status.recent_tasks || []);
    renderHero();
    await loadNotifications($('#notifications-filter').dataset.unread === 'true');
  } catch (error) {
    setLink('offline');
    throw error;
  }
}

function renderHealthCards() {
  const container = $('#health-cards');
  clearNode(container);
  const health = STATE.health || {};
  const status = STATE.status || {};
  const model = health.model || {};
  const counts = status.counts || {};
  const cards = [
    {
      label: 'Core',
      value: (health.status || 'unknown').toUpperCase(),
      sub: 'uptime ' + Math.round(health.uptime_seconds || 0) + 's',
      kind: health.status === 'ok' ? 'ok' : 'warn',
    },
    {
      label: 'Model',
      value: model.status || 'unknown',
      sub: model.configured ? model.model : 'no local model configured',
      kind: model.status === 'working' ? 'ok' : model.status === 'unconfigured' ? 'warn' : 'bad',
    },
    {
      label: 'Tasks',
      value: String(counts.tasks || 0),
      sub: (status.running_tasks || 0) + ' running',
      kind: '',
    },
    {
      label: 'Integrations',
      value: (health.integrations_configured || 0) + '/' + (health.integrations_total || 0),
      sub: 'configured',
      kind: '',
    },
    {
      label: 'Memory',
      value: String(counts.memory_items || 0),
      sub: 'governed items',
      kind: '',
    },
    {
      label: 'Safety',
      value: STATE.emergency ? 'STOPPED' : 'ARMED',
      sub: 'autonomy L' + ((STATE.settings && STATE.settings.autonomy_level) || 2),
      kind: STATE.emergency ? 'bad' : 'ok',
    },
  ];
  cards.forEach((card) => {
    const node = el('div', 'card ' + (card.kind || ''));
    node.appendChild(el('div', 'label', card.label));
    node.appendChild(el('div', 'value', card.value));
    node.appendChild(el('div', 'sub', card.sub));
    container.appendChild(node);
  });
}

function renderRecentTasks(tasks) {
  const container = $('#recent-tasks');
  clearNode(container);
  if (!tasks.length) {
    container.appendChild(el('div', 'empty', 'No tasks yet. Ask MATRIX for a system status check.'));
    return;
  }
  tasks.slice(0, 8).forEach((task) => {
    const item = el('div', 'item clickable');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', truncate(task.original_request, 110)));
    const sub = el('div', 'item-sub');
    sub.textContent = `${shortId(task.id)} · ${task.plan_source || 'plan'} · ${fmtDate(task.created_at)}`;
    main.appendChild(sub);
    item.appendChild(main);
    const actions = el('div', 'item-actions');
    actions.appendChild(statePill(task.state));
    item.appendChild(actions);
    item.addEventListener('click', () => {
      switchView('tasks');
      openTask(task.id);
    });
    container.appendChild(item);
  });
}

function renderHero() {
  const health = STATE.health || {};
  const model = health.model || {};
  const title = $('#hero-title');
  const text = $('#hero-text');
  const coreState = $('#core-state');
  if (STATE.emergency) {
    title.textContent = 'Emergency stop active';
    text.textContent = 'All tool execution is blocked until you clear the stop in Security.';
    setCoreState('STOPPED');
    return;
  }
  if (health.status === 'ok') {
    title.textContent = 'Systems nominal';
  } else if (health.status === 'degraded') {
    title.textContent = 'Degraded mode';
  } else {
    title.textContent = 'Standing by';
  }
  const bits = [];
  bits.push(model.status === 'working' ? 'local model ready' : 'deterministic tools only');
  bits.push((STATE.status && STATE.status.running_tasks) || 0 ? 'task running' : 'idle');
  text.textContent =
    'Local-first, policy-checked assistant. ' +
    bits.join(' · ') +
    '. Sensitive actions stop for your exact approval.';
  coreState.textContent = (STATE.status && STATE.status.running_tasks) ? 'WORKING' : 'IDLE';
  setCoreState(coreState.textContent);
}

function setCoreState(state) {
  STATE.coreState = state;
  $$('.core-state').forEach((node) => {
    node.textContent = state;
  });
}

$$('[data-quick]').forEach((button) => {
  button.addEventListener('click', () => {
    $('#chat-input').value = button.dataset.quick;
    switchView('chat');
    $('#chat-form').requestSubmit();
  });
});

/* ------------------------------------------------------------------ */
/* chat                                                                */
/* ------------------------------------------------------------------ */
function appendMessage(role, body, meta = []) {
  const wrapper = el('div', 'msg ' + role);
  wrapper.appendChild(el('div', 'who', role === 'user' ? 'You' : 'MATRIX'));
  const content = el('div', 'body', body);
  wrapper.appendChild(content);
  const metaRow = el('div', 'meta');
  meta.forEach((node) => metaRow.appendChild(node));
  wrapper.appendChild(metaRow);
  $('#chat-log').appendChild(wrapper);
  $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
  return wrapper;
}

function setMessageBody(node, text) {
  const body = $('.body', node);
  if (body) body.textContent = text;
  $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
}

function setMessageMeta(node, task) {
  const meta = $('.meta', node);
  clearNode(meta);
  meta.appendChild(statePill(task.state));
  meta.appendChild(pill(shortId(task.id), ''));
  const evidence = task.evidence || null;
  if (task.summary) meta.appendChild(pill(truncate(task.summary, 64), ''));
}

async function pollTask(taskId, onUpdate, timeoutMs = 180000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const task = await api('/tasks/' + taskId);
    if (onUpdate) onUpdate(task);
    if (TERMINAL.has(task.state)) return task;
    await sleep(600);
  }
  throw new ApiError('timeout', 'Task did not finish in time');
}

$('#chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('#chat-input');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  appendMessage('user', message);
  const pending = appendMessage('assistant', 'Working…', [pill('RECEIVED', 'live')]);
  try {
    const response = await api('/chat', { method: 'POST', body: { message } });
    const task = await pollTask(response.task.id, (update) => {
      setMessageBody(pending, update.summary || 'Working…');
      setMessageMeta(pending, update);
    });
    setMessageBody(pending, task.summary || '(no summary returned)');
    setMessageMeta(pending, task);
    const pendingApproval = (task.approvals || []).find((entry) => entry.status === 'pending');
    if (pendingApproval) {
      appendApproval(task);
    }
    await loadDashboard();
  } catch (error) {
    setMessageBody(pending, 'Request failed: ' + error.message);
    setMessageMeta(pending, { state: 'FAILED', id: '', summary: '' });
    toast(error.message, 'error');
  }
});

function appendApproval(task) {
  const approval = (task.approvals || []).find((entry) => entry.status === 'pending');
  if (!approval) return;
  const card = el('div', 'approval');
  card.appendChild(el('div', 'title', 'Approval required · ' + approval.tool_name));
  const meta = el('div', 'meta');
  meta.appendChild(riskChip(approval.risk));
  meta.appendChild(pill('expires ' + fmtTime(approval.expires_at), 'warn'));
  meta.appendChild(pill('task ' + shortId(task.id), ''));
  card.appendChild(meta);
  const args = el('pre', 'code', pretty(approval.arguments));
  card.appendChild(args);
  card.appendChild(
    el('div', 'hint', 'Approval is bound to these exact arguments. Denying executes nothing.'),
  );
  const row = el('div', 'row');
  const approve = el('button', '', 'Approve once');
  const deny = el('button', 'ghost', 'Deny');
  row.appendChild(approve);
  row.appendChild(deny);
  card.appendChild(row);

  async function decide(approved) {
    approve.disabled = true;
    deny.disabled = true;
    try {
      await api(`/tasks/${task.id}/approvals/${approval.id}/${approved ? 'approve' : 'deny'}`, {
        method: 'POST',
        body: { reason: approved ? 'approved from dashboard' : 'denied from dashboard' },
      });
      toast(approved ? 'Approved. Executing…' : 'Denied. Nothing was executed.', approved ? 'ok' : 'info');
      const finalTask = await pollTask(task.id);
      appendMessage('assistant', finalTask.summary || '(no summary)', [statePill(finalTask.state)]);
      card.remove();
      await loadDashboard();
    } catch (error) {
      toast(error.message, 'error');
      approve.disabled = false;
      deny.disabled = false;
    }
  }
  approve.addEventListener('click', () => decide(true));
  deny.addEventListener('click', () => decide(false));
  $('#chat-log').appendChild(card);
  $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
}

/* ------------------------------------------------------------------ */
/* tasks                                                               */
/* ------------------------------------------------------------------ */
async function loadTasks() {
  STATE.tasks = await api('/tasks?limit=60');
  renderTaskList();
}

function renderTaskList() {
  const container = $('#task-list');
  clearNode(container);
  if (!STATE.tasks.length) {
    container.appendChild(el('div', 'empty', 'No tasks yet.'));
    return;
  }
  STATE.tasks.forEach((task) => {
    const item = el('div', 'item clickable' + (STATE.selectedTaskId === task.id ? ' selected' : ''));
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', truncate(task.original_request, 96)));
    main.appendChild(
      el('div', 'item-sub', `${shortId(task.id)} · ${fmtDate(task.created_at)} · ${task.plan_source || 'plan'}`),
    );
    item.appendChild(main);
    const actions = el('div', 'item-actions');
    actions.appendChild(statePill(task.state));
    item.appendChild(actions);
    item.addEventListener('click', () => openTask(task.id));
    container.appendChild(item);
  });
}

async function openTask(taskId) {
  try {
    const task = await api('/tasks/' + taskId);
    STATE.selectedTaskId = taskId;
    $('#task-detail-id').textContent = shortId(taskId);
    renderTaskDetail(task);
    renderTaskList();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderTaskDetail(task) {
  const container = $('#task-detail');
  clearNode(container);

  const header = el('div', 'item');
  const main = el('div', 'item-main');
  main.appendChild(el('div', 'item-title', truncate(task.original_request, 200)));
  main.appendChild(
    el('div', 'item-sub', `${fmtDate(task.created_at)} · risk ${task.risk || 'n/a'} · autonomy L${task.autonomy_level}`),
  );
  header.appendChild(main);
  header.appendChild(statePill(task.state));
  container.appendChild(header);

  if (task.summary) {
    const block = el('div', 'detail-block');
    block.appendChild(el('h4', '', 'Summary'));
    block.appendChild(el('div', 'body-text', task.summary));
    container.appendChild(block);
  }
  if (task.error) {
    const block = el('div', 'detail-block');
    block.appendChild(el('h4', '', 'Error'));
    block.appendChild(el('pre', 'code', task.error));
    container.appendChild(block);
  }

  const stepsBlock = el('div', 'detail-block');
  stepsBlock.appendChild(el('h4', '', 'Plan steps'));
  const list = el('ul', 'steps');
  (task.steps || []).forEach((step) => {
    const li = document.createElement('li');
    li.appendChild(el('span', 'idx', String(step.idx + 1).padStart(2, '0')));
    const label = el('span', '', step.title + (step.tool_name ? ' · ' + step.tool_name : ''));
    li.appendChild(label);
    li.appendChild(el('span', 'status', step.status));
    list.appendChild(li);
  });
  stepsBlock.appendChild(list);
  container.appendChild(stepsBlock);

  if ((task.tool_calls || []).length) {
    const callsBlock = el('div', 'detail-block');
    callsBlock.appendChild(el('h4', '', 'Tool trace'));
    const calls = el('div', 'list');
    task.tool_calls.forEach((call) => {
      const item = el('div', 'item');
      const main = el('div', 'item-main');
      main.appendChild(el('div', 'item-title', `${call.tool_name} · attempt ${call.attempt}`));
      main.appendChild(
        el(
          'div',
          'item-sub',
          `${Math.round(call.duration_ms || 0)} ms · ${call.status}` + (call.error ? ' · ' + truncate(call.error, 80) : ''),
        ),
      );
      if (call.evidence) {
        main.appendChild(
          el(
            'div',
            'item-sub',
            `evidence: ${call.evidence.ok ? 'verified' : 'not verified'} (${(call.evidence.checks || []).join(', ')})`,
          ),
        );
      }
      item.appendChild(main);
      item.appendChild(riskChip(call.risk));
      calls.appendChild(item);
    });
    callsBlock.appendChild(calls);
    container.appendChild(callsBlock);
  }

  const evidence = task.evidence || null;
  if (evidence) {
    const block = el('div', 'detail-block');
    block.appendChild(el('h4', '', 'Verification'));
    block.appendChild(el('pre', 'code', pretty(evidence)));
    container.appendChild(block);
  }

  (task.approvals || []).forEach((approval) => {
    if (approval.status !== 'pending') return;
    const block = el('div', 'approval');
    block.appendChild(el('div', 'title', 'Approval required · ' + approval.tool_name));
    block.appendChild(el('pre', 'code', pretty(approval.arguments)));
    const row = el('div', 'row');
    const approve = el('button', '', 'Approve once');
    const deny = el('button', 'ghost', 'Deny');
    async function decide(approved) {
      approve.disabled = true;
      deny.disabled = true;
      try {
        await api(`/tasks/${task.id}/approvals/${approval.id}/${approved ? 'approve' : 'deny'}`, {
          method: 'POST',
          body: {},
        });
        toast(approved ? 'Approved' : 'Denied', approved ? 'ok' : 'info');
        await openTask(task.id);
      } catch (error) {
        toast(error.message, 'error');
      }
    }
    approve.addEventListener('click', () => decide(true));
    deny.addEventListener('click', () => decide(false));
    row.appendChild(approve);
    row.appendChild(deny);
    block.appendChild(row);
    container.appendChild(block);
  });

  if (!TERMINAL.has(task.state)) {
    const row = el('div', 'row');
    const cancel = el('button', 'danger', 'Cancel task');
    cancel.addEventListener('click', async () => {
      try {
        await api(`/tasks/${task.id}/cancel`, { method: 'POST' });
        toast('Cancellation requested', 'info');
        await openTask(task.id);
      } catch (error) {
        toast(error.message, 'error');
      }
    });
    row.appendChild(cancel);
    const refresh = el('button', 'ghost', 'Refresh');
    refresh.addEventListener('click', () => openTask(task.id));
    row.appendChild(refresh);
    container.appendChild(row);
  }
}

/* ------------------------------------------------------------------ */
/* memory                                                              */
/* ------------------------------------------------------------------ */
async function loadMemory(query) {
  const path = query
    ? '/memory/search?q=' + encodeURIComponent(query)
    : '/memory?limit=60';
  const result = await api(path);
  const items = query ? result.items : result;
  renderMemory(items || []);
}

function renderMemory(items) {
  const container = $('#memory-list');
  clearNode(container);
  if (!items.length) {
    container.appendChild(el('div', 'empty', 'No memory items.'));
    return;
  }
  items.forEach((item) => {
    const node = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', item.title));
    main.appendChild(el('div', 'item-sub', `${item.kind} · ${item.source} · confidence ${item.confidence}`));
    main.appendChild(el('div', 'item-sub', truncate(item.content, 220)));
    node.appendChild(main);
    const actions = el('div', 'item-actions');
    const del = el('button', 'ghost small', 'Delete');
    del.addEventListener('click', async () => {
      if (!confirm('Delete this memory item permanently?')) return;
      try {
        await api('/memory/' + item.id, { method: 'DELETE' });
        toast('Memory deleted', 'ok');
        await loadMemory($('#memory-search').value.trim());
      } catch (error) {
        toast(error.message, 'error');
      }
    });
    actions.appendChild(del);
    node.appendChild(actions);
    container.appendChild(node);
  });
}

$('#memory-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/memory', {
      method: 'POST',
      body: {
        kind: $('#memory-kind').value,
        title: $('#memory-title').value.trim(),
        content: $('#memory-content').value.trim(),
        source: 'dashboard',
      },
    });
    $('#memory-title').value = '';
    $('#memory-content').value = '';
    toast('Memory stored', 'ok');
    await loadMemory($('#memory-search').value.trim());
  } catch (error) {
    toast(error.message, 'error');
  }
});

$('#memory-search-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  await loadMemory($('#memory-search').value.trim()).catch((error) => toast(error.message, 'error'));
});

$('#memory-export').addEventListener('click', async () => {
  try {
    const payload = await api('/memory/export');
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'matrix-memory-export.json';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    toast('Exported ' + payload.count + ' items', 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
});

/* ------------------------------------------------------------------ */
/* voice                                                               */
/* ------------------------------------------------------------------ */
async function loadVoice() {
  const status = await api('/voice/status');
  const container = $('#voice-cards');
  clearNode(container);
  const cards = [
    ['Session', status.state, status.available ? 'server-side session' : 'unavailable'],
    ['Microphone', status.muted ? 'MUTED' : status.state === 'idle' ? 'idle' : 'LIVE', status.muted ? 'capture refused' : 'capture allowed'],
    ['Speech to text', status.stt_provider, 'engine'],
    ['Text to speech', status.tts_provider, 'engine'],
  ];
  cards.forEach(([label, value, sub]) => {
    const card = el('div', 'card');
    card.appendChild(el('div', 'label', label));
    card.appendChild(el('div', 'value', String(value)));
    card.appendChild(el('div', 'sub', sub));
    container.appendChild(card);
  });
  const indicator = $('#mic-indicator');
  if (status.muted) {
    indicator.className = 'pill bad';
    indicator.textContent = 'microphone muted';
  } else if (status.state === 'listening') {
    indicator.className = 'pill live';
    indicator.textContent = 'microphone live';
  } else if (status.state === 'speaking') {
    indicator.className = 'pill live';
    indicator.textContent = 'speaking';
  } else {
    indicator.className = 'pill';
    indicator.textContent = 'microphone idle';
  }
  $('#voice-detail').textContent =
    status.detail || 'Microphone state is server-side. A muted session refuses capture with HTTP 409.';
}

async function voiceAction(path, body = {}, okMessage) {
  try {
    const result = await api('/voice/' + path, { method: 'POST', body });
    if (okMessage) toast(okMessage, 'ok');
    await loadVoice();
    return result;
  } catch (error) {
    toast(error.message, 'error');
    return null;
  }
}

$('#voice-start').addEventListener('click', () => voiceAction('start', { muted: false }, 'Session started'));
$('#voice-mute').addEventListener('click', () => voiceAction('mute', {}, 'Microphone muted'));
$('#voice-unmute').addEventListener('click', () => voiceAction('unmute', {}, 'Microphone unmuted'));
$('#voice-stop').addEventListener('click', () => voiceAction('stop', {}, 'Session stopped'));
$('#voice-barge').addEventListener('click', async () => {
  const result = await voiceAction('barge-in');
  if (result) toast(result.interrupted ? 'Speech interrupted' : 'Nothing was speaking', 'info');
});
$('#voice-speak-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = $('#voice-speak-text').value.trim();
  if (!text) return;
  const result = await voiceAction('speak', { text });
  if (result) toast('Speaking', 'ok');
});

/* ------------------------------------------------------------------ */
/* vision                                                              */
/* ------------------------------------------------------------------ */
let visionSessionId = null;

async function loadVision() {
  const status = await api('/vision/status');
  const container = $('#vision-cards');
  clearNode(container);
  const cards = [
    ['Capture', status.state, status.active_sessions + ' active session(s)'],
    ['OCR', status.ocr_available ? 'available' : 'unavailable', status.ocr_available ? 'Tesseract detected' : 'needs Tesseract'],
    ['Persistence', 'disabled', 'frames are not stored'],
  ];
  cards.forEach(([label, value, sub]) => {
    const card = el('div', 'card');
    card.appendChild(el('div', 'label', label));
    card.appendChild(el('div', 'value', String(value)));
    card.appendChild(el('div', 'sub', sub));
    container.appendChild(card);
  });
  const indicator = $('#vision-indicator');
  indicator.className = 'pill ' + (status.state === 'active' ? 'live' : '');
  indicator.textContent = status.state;
  $('#vision-detail').textContent =
    status.detail || 'Frames are processed on demand and never persisted by default.';
}

$('#vision-start').addEventListener('click', async () => {
  try {
    const session = await api('/vision/sessions', {
      method: 'POST',
      body: { permission_granted: $('#vision-permission').checked },
    });
    visionSessionId = session.session_id;
    toast('Capture session started', 'ok');
    await loadVision();
  } catch (error) {
    toast(error.message, 'error');
  }
});

$('#vision-capture').addEventListener('click', async () => {
  if (!visionSessionId) {
    toast('Start a session first', 'error');
    return;
  }
  try {
    const frame = await api(`/vision/sessions/${visionSessionId}/capture`, { method: 'POST' });
    toast('Captured frame ' + frame.frame + ' (not persisted)', 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
});

$('#vision-stop').addEventListener('click', async () => {
  if (!visionSessionId) {
    toast('No active session', 'error');
    return;
  }
  try {
    await api(`/vision/sessions/${visionSessionId}/stop`, { method: 'POST' });
    visionSessionId = null;
    toast('Session stopped', 'ok');
    await loadVision();
  } catch (error) {
    toast(error.message, 'error');
  }
});

/* ------------------------------------------------------------------ */
/* agents                                                              */
/* ------------------------------------------------------------------ */
async function loadAgents() {
  STATE.agents = await api('/agents');
  const container = $('#agents-grid');
  clearNode(container);
  STATE.agents.forEach((agent) => {
    const card = el('div', 'agent');
    card.appendChild(el('div', 'name', agent.name));
    card.appendChild(el('div', 'desc', agent.responsibility));
    card.appendChild(el('div', 'tools', (agent.tools || []).join(' · ')));
    container.appendChild(card);
  });
}

/* ------------------------------------------------------------------ */
/* tools                                                               */
/* ------------------------------------------------------------------ */
async function loadTools() {
  STATE.tools = await api('/tools');
  const container = $('#tool-list');
  const select = $('#tool-select');
  clearNode(container);
  clearNode(select);
  STATE.tools.forEach((tool) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', tool.name));
    main.appendChild(el('div', 'item-sub', tool.description));
    main.appendChild(
      el(
        'div',
        'item-sub',
        `${tool.permission_scope} · ${tool.timeout_seconds}s · ${tool.idempotent ? 'idempotent' : 'non-idempotent'}`,
      ),
    );
    item.appendChild(main);
    item.appendChild(riskChip(tool.risk));
    container.appendChild(item);

    const option = document.createElement('option');
    option.value = tool.name;
    option.textContent = `${tool.name} · ${tool.risk}`;
    select.appendChild(option);
  });
}

$('#tool-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const toolName = $('#tool-select').value;
  let args = {};
  try {
    args = JSON.parse($('#tool-args').value || '{}');
  } catch (error) {
    toast('Arguments must be valid JSON', 'error');
    return;
  }
  const scopesRaw = $('#tool-scopes').value.trim();
  const scopes = scopesRaw ? scopesRaw.split(',').map((scope) => scope.trim()).filter(Boolean) : null;
  const output = $('#tool-result');
  output.textContent = 'Running…';
  try {
    const task = await api(`/tools/${toolName}/execute`, {
      method: 'POST',
      body: { arguments: args, scopes },
    });
    const finalTask = await pollTask(task.id, (update) => {
      output.textContent = `State: ${update.state}`;
    });
    clearNode(output);
    const summary = el('div', finalTask.state === 'COMPLETE' ? 'ok' : 'bad', finalTask.summary || '');
    output.appendChild(summary);
    const calls = finalTask.tool_calls || [];
    if (calls.length) output.appendChild(el('pre', 'code', pretty(calls.map((call) => call.output))));
    if ((finalTask.approvals || []).some((entry) => entry.status === 'pending')) {
      output.appendChild(el('div', 'hint', 'This action needs approval — open the task detail to decide.'));
    }
  } catch (error) {
    output.textContent = 'Failed: ' + error.message;
    toast(error.message, 'error');
  }
});

/* ------------------------------------------------------------------ */
/* devices                                                             */
/* ------------------------------------------------------------------ */
async function loadDevices() {
  STATE.devices = await api('/devices');
  const container = $('#device-list');
  clearNode(container);
  if (!STATE.devices.length) {
    container.appendChild(el('div', 'empty', 'No paired devices.'));
    return;
  }
  STATE.devices.forEach((device) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', device.name + (device.revoked ? ' (revoked)' : '')));
    main.appendChild(
      el('div', 'item-sub', `${device.platform} · paired ${fmtDate(device.paired_at)} · ${shortId(device.id)}`),
    );
    item.appendChild(main);
    if (!device.revoked) {
      const revoke = el('button', 'ghost small', 'Revoke');
      revoke.addEventListener('click', async () => {
        if (!confirm('Revoke this device? Its token stops working immediately.')) return;
        try {
          await api(`/devices/${device.id}/revoke`, { method: 'POST' });
          toast('Device revoked', 'ok');
          await loadDevices();
        } catch (error) {
          toast(error.message, 'error');
        }
      });
      const actions = el('div', 'item-actions');
      actions.appendChild(revoke);
      item.appendChild(actions);
    }
    container.appendChild(item);
  });
}

$('#pairing-code-btn').addEventListener('click', async () => {
  try {
    const view = await api('/auth/pairing-code', { method: 'POST' });
    const output = $('#pairing-output');
    clearNode(output);
    output.appendChild(el('div', 'value mono', view.code));
    output.appendChild(el('div', 'hint', 'Expires at ' + fmtDate(view.expires_at) + '. Single use.'));
  } catch (error) {
    toast(error.message, 'error');
  }
});

/* ------------------------------------------------------------------ */
/* integrations                                                        */
/* ------------------------------------------------------------------ */
async function loadIntegrations() {
  STATE.integrations = await api('/integrations');
  const container = $('#integrations-grid');
  clearNode(container);
  STATE.integrations.forEach((integration) => {
    const card = el('div', 'integration');
    const head = el('div', 'row');
    head.appendChild(el('div', 'name', integration.name));
    const kind = integration.status === 'working' ? 'ok' : integration.status === 'configured' ? 'live' : integration.status === 'missing_credentials' ? 'warn' : 'bad';
    head.appendChild(pill(integration.status, kind));
    card.appendChild(head);
    card.appendChild(el('div', 'desc', integration.description));
    if (integration.detail) card.appendChild(el('div', 'desc', integration.detail));
    const foot = el('div', 'foot');
    foot.appendChild(el('span', 'mono muted', integration.category));
    const check = el('button', 'ghost small', 'Check');
    check.addEventListener('click', async () => {
      check.disabled = true;
      try {
        const updated = await api(`/integrations/${integration.name}/check`, { method: 'POST' });
        toast(`${updated.name}: ${updated.status}`, updated.status === 'working' ? 'ok' : 'info');
        await loadIntegrations();
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        check.disabled = false;
      }
    });
    foot.appendChild(check);
    card.appendChild(foot);
    container.appendChild(card);
  });
}

/* ------------------------------------------------------------------ */
/* security                                                            */
/* ------------------------------------------------------------------ */
async function loadSecurity() {
  const [policy, audit] = await Promise.all([api('/security/policy'), api('/security/audit?limit=120')]);
  STATE.policy = policy;
  STATE.emergency = policy.emergency_stop;
  const container = $('#policy-list');
  clearNode(container);
  [
    ['Emergency stop', policy.emergency_stop ? 'ACTIVE' : 'off', ''],
    ['Autonomy level', 'L' + policy.autonomy_level, ''],
    ['Approval TTL', policy.approval_ttl_seconds + 's', 'exact action binding'],
  ].forEach(([label, value, sub]) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', label + ': ' + value));
    if (sub) main.appendChild(el('div', 'item-sub', sub));
    item.appendChild(main);
    container.appendChild(item);
  });
  policy.tools.forEach((tool) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', tool.name));
    main.appendChild(
      el('div', 'item-sub', `${tool.permission_scope} · ${tool.requires_approval ? 'approval required' : 'auto within scope'}`),
    );
    item.appendChild(main);
    item.appendChild(riskChip(tool.risk));
    container.appendChild(item);
  });

  const emergency = $('#emergency-btn');
  emergency.textContent = policy.emergency_stop ? 'Clear emergency stop' : 'Activate emergency stop';
  emergency.onclick = async () => {
    const activating = !STATE.emergency;
    if (activating && !confirm('Stop all tool execution and cancel running tasks?')) return;
    try {
      const result = await api('/security/emergency-stop', { method: 'POST', body: { active: activating } });
      toast(activating ? `Emergency stop active (${result.cancelled_tasks} cancelled)` : 'Emergency stop cleared', 'ok');
      await loadSecurity();
      await loadDashboard();
    } catch (error) {
      toast(error.message, 'error');
    }
  };

  const auditList = $('#audit-list');
  clearNode(auditList);
  audit.forEach((event) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', event.event_type));
    const bits = [fmtTime(event.ts), event.actor, event.status].filter(Boolean);
    main.appendChild(el('div', 'item-sub', bits.join(' · ')));
    item.appendChild(main);
    auditList.appendChild(item);
  });
}

$('#stop-btn').addEventListener('click', async () => {
  if (!confirm('Activate emergency stop? Running tasks will be cancelled.')) return;
  try {
    const result = await api('/security/emergency-stop', { method: 'POST', body: { active: true } });
    toast(`Emergency stop active (${result.cancelled_tasks} cancelled)`, 'ok');
    await loadDashboard();
  } catch (error) {
    toast(error.message, 'error');
  }
});

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */
async function loadSettings() {
  const [settings, models] = await Promise.all([api('/settings'), api('/models')]);
  await loadNotificationPreferences();
  STATE.settings = settings;
  STATE.models = models;
  $('#settings-json').textContent = pretty(settings);
  $('#hardware-json').textContent = pretty(models.hardware);
  const container = $('#model-status');
  clearNode(container);
  const status = models.status;
  [
    ['Provider', status.provider],
    ['Model', status.model],
    ['Status', status.status],
    ['Configured', status.configured ? 'yes' : 'no'],
    ['Reachable', status.reachable ? 'yes' : 'no'],
    ['RAM budget', status.ram_budget_mb + ' MB'],
    ['Last latency', status.last_latency_ms ? Math.round(status.last_latency_ms) + ' ms' : '—'],
  ].forEach(([label, value]) => {
    const item = el('div', 'item');
    const main = el('div', 'item-main');
    main.appendChild(el('div', 'item-title', label + ': ' + value));
    item.appendChild(main);
    container.appendChild(item);
  });
  if (status.detail) {
    container.appendChild(el('div', 'hint', status.detail));
  }
}

/* ------------------------------------------------------------------ */
/* refresh wiring                                                      */
/* ------------------------------------------------------------------ */
const REFRESH = {
  tasks: loadTasks,
  tools: loadTools,
  devices: loadDevices,
  integrations: loadIntegrations,
  policy: loadSecurity,
  audit: loadSecurity,
  settings: loadSettings,
  models: loadSettings,
};
$$('[data-refresh]').forEach((button) => {
  button.addEventListener('click', async () => {
    const loader = REFRESH[button.dataset.refresh];
    if (!loader) return;
    try {
      await loader();
    } catch (error) {
      toast(error.message, 'error');
    }
  });
});

/* ------------------------------------------------------------------ */
/* JARVIS core animation                                               */
/* ------------------------------------------------------------------ */
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function initCore(canvas, { hero = false } = {}) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const ratio = window.devicePixelRatio || 1;
  const cssSize = canvas.clientWidth || (hero ? 200 : 52);
  canvas.width = cssSize * ratio;
  canvas.height = cssSize * ratio;
  ctx.scale(ratio, ratio);
  const center = cssSize / 2;
  const radius = cssSize / 2 - (hero ? 14 : 4);
  const particles = Array.from({ length: hero ? 26 : 10 }, () => ({
    angle: Math.random() * Math.PI * 2,
    speed: 0.0015 + Math.random() * 0.004,
    size: 0.7 + Math.random() * 1.3,
    ring: 0.55 + Math.random() * 0.4,
  }));
  let tick = 0;
  let raf = null;

  function accentColor() {
    if (STATE.coreState === 'STOPPED') return '#ff6f6f';
    if (STATE.emergency) return '#f0b95a';
    return '#35d0e6';
  }

  function draw() {
    ctx.clearRect(0, 0, cssSize, cssSize);
    const accent = accentColor();
    ctx.save();
    ctx.translate(center, center);

    const glow = ctx.createRadialGradient(0, 0, radius * 0.1, 0, 0, radius);
    glow.addColorStop(0, 'rgba(53, 208, 230, 0.16)');
    glow.addColorStop(0.65, 'rgba(53, 208, 230, 0.04)');
    glow.addColorStop(1, 'rgba(53, 208, 230, 0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(0, 0, radius, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = 'rgba(126, 168, 188, 0.28)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(0, 0, radius * 0.94, 0, Math.PI * 2);
    ctx.stroke();

    for (let index = 0; index < 3; index += 1) {
      const offset = tick * (0.006 + index * 0.003) * (index % 2 === 0 ? 1 : -1);
      const start = offset + index * 2.1;
      const span = Math.PI * (0.55 + index * 0.12);
      ctx.strokeStyle = accent;
      ctx.globalAlpha = 0.55 - index * 0.12;
      ctx.lineWidth = hero ? 2.2 - index * 0.4 : 1.6 - index * 0.3;
      ctx.beginPath();
      ctx.arc(0, 0, radius * (0.82 - index * 0.17), start, start + span);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    const ticks = hero ? 36 : 20;
    for (let index = 0; index < ticks; index += 1) {
      const angle = (index / ticks) * Math.PI * 2 + tick * 0.001;
      const inner = radius * 0.88;
      const outer = radius * (index % 5 === 0 ? 0.83 : 0.86);
      ctx.strokeStyle = index % 5 === 0 ? accent : 'rgba(126, 168, 188, 0.4)';
      ctx.globalAlpha = index % 5 === 0 ? 0.7 : 0.35;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * inner, Math.sin(angle) * inner);
      ctx.lineTo(Math.cos(angle) * outer, Math.sin(angle) * outer);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    particles.forEach((particle) => {
      particle.angle += reducedMotion.matches ? 0 : particle.speed;
      const distance = radius * particle.ring;
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.55;
      ctx.beginPath();
      ctx.arc(
        Math.cos(particle.angle) * distance,
        Math.sin(particle.angle) * distance,
        particle.size,
        0,
        Math.PI * 2,
      );
      ctx.fill();
    });
    ctx.globalAlpha = 1;

    ctx.fillStyle = accent;
    ctx.beginPath();
    ctx.arc(0, 0, hero ? 7 : 3.2, 0, Math.PI * 2);
    ctx.fill();

    if (hero) {
      ctx.fillStyle = 'rgba(219, 231, 238, 0.55)';
      ctx.font = '10px "Cascadia Mono", Consolas, monospace';
      ctx.textAlign = 'center';
      ctx.fillText('MATRIX CORE', 0, radius * 0.42);
    }

    ctx.restore();
    tick += 1;
  }

  function loop() {
    draw();
    raf = window.requestAnimationFrame(loop);
  }

  if (reducedMotion.matches) {
    draw();
    reducedMotion.addEventListener('change', () => {
      if (reducedMotion.matches) {
        if (raf) window.cancelAnimationFrame(raf);
        raf = null;
        draw();
      } else {
        loop();
      }
    });
  } else {
    loop();
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (raf) window.cancelAnimationFrame(raf);
      raf = null;
    } else if (!reducedMotion.matches && !raf) {
      loop();
    }
  });
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */
function tickClock() {
  const now = new Date();
  $('#clock').textContent = now.toLocaleTimeString();
}

async function refreshHealth() {
  try {
    const health = await api('/health');
    STATE.health = health;
    setLink(health.status === 'ok' ? 'linked' : 'degraded');
    renderHealthCards();
  } catch (error) {
    setLink('offline');
  }
}

async function boot() {
  if (!STATE.token) {
    showLogin();
    return;
  }
  try {
    await api('/auth/me');
  } catch (error) {
    return;
  }
  hideLogin();
  tickClock();
  setInterval(tickClock, 1000);
  try {
    STATE.settings = await api('/settings');
    await loadDashboard();
    await Promise.allSettled([loadTools(), loadAgents(), loadIntegrations()]);
  } catch (error) {
    toast(error.message, 'error');
  }
  setInterval(() => {
    if (!document.hidden) refreshHealth();
  }, 15000);
  STATE.booted = true;
}

initCore($('#core-canvas'));
initCore($('#hero-canvas'), { hero: true });
boot();
