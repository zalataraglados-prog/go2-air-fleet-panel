const token = document.querySelector('meta[name="go2-panel-token"]').content;
const draftStorageKey = 'go2_choreography_v3';
const preferencesStorageKey = 'go2_panel_preferences_v1';
const ui = Object.fromEntries([
  'connectionBadge', 'connectionTitle', 'connectionDetail',
  'stopButton', 'clearanceCheck', 'highRiskPhrase', 'activityList',
  'clearActivity', 'actionLibrary',
  'libraryFilters', 'editorAction', 'editorDuration', 'addStep', 'timeline',
  'timelineTotal', 'loadFeatured', 'featuredDescription', 'saveDraft', 'loadDraft',
  'clearTimeline', 'runChoreography', 'customKind', 'customEulerFields',
  'customRoll', 'customPitch', 'customYaw',
  'customDuration', 'addCustomStep',
  'selectionSummary', 'selectionBoard', 'selectionMarquee', 'fleetRobots',
  'selectAllButton',
  'clearSelectionButton',
].map((id) => [id, document.querySelector(`#${id}`)]));

function loadPreferences() {
  const defaults = {
    autoConnect: true,
    initialSelection: 'all',
    pollIntervalMs: 2000,
    compactCards: false,
  };
  try {
    const saved = JSON.parse(localStorage.getItem(preferencesStorageKey) || '{}');
    const initialSelection = ['all', 'online', 'none'].includes(saved.initialSelection)
      ? saved.initialSelection
      : defaults.initialSelection;
    const pollIntervalMs = [1000, 2000, 5000].includes(Number(saved.pollIntervalMs))
      ? Number(saved.pollIntervalMs)
      : defaults.pollIntervalMs;
    return {
      autoConnect: saved.autoConnect !== false,
      initialSelection,
      pollIntervalMs,
      compactCards: saved.compactCards === true,
    };
  } catch {
    return defaults;
  }
}

const preferences = loadPreferences();
document.body.classList.toggle('compact-cards', preferences.compactCards);

let current = { connected: false, busy: false, operation: null, actions: [], action_results: {}, featured_choreography: null, fleet: { robots: [], configured_count: 0, connected_count: 0 } };
let selectedRobotIds = new Set();
let selectionInitialized = false;
let timeline = [];
let activeFilter = 'all';
let librarySignature = '';
let featuredAutoLoaded = false;
let marqueeDrag = null;
let autoConnectInFlight = false;
let nextAutoConnectAt = 0;

function now() { return new Date().toLocaleTimeString('zh-CN', { hour12: false }); }

function log(message, kind = '') {
  const item = document.createElement('li');
  if (kind) item.className = kind;
  const time = document.createElement('time'); time.textContent = now();
  const copy = document.createElement('span'); copy.textContent = message;
  item.append(time, copy); ui.activityList.prepend(item);
  while (ui.activityList.children.length > 30) ui.activityList.lastElementChild.remove();
}

async function api(path, { method = 'GET', body = null } = {}) {
  const options = { method, headers: {} };
  if (method !== 'GET') {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-Go2-Panel-Token'] = token;
    options.body = JSON.stringify(body || {});
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ error: '面板返回了无法解析的响应。' }));
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function configuredRobots() {
  return current.fleet?.robots || [];
}

function selectedTargets() {
  return configuredRobots()
    .map((robot) => robot.id)
    .filter((robotId) => selectedRobotIds.has(robotId));
}

function selectedRobots() {
  return configuredRobots().filter((robot) => selectedRobotIds.has(robot.id));
}

function selectionIsOnline() {
  const selected = selectedRobots();
  return selected.length > 0 && selected.every((robot) => robot.connected);
}

function actionControllerReady() {
  const selected = selectedRobots();
  return selected.length > 0 && selected.every((robot) => (
    robot.state && !(robot.state.mode === 0 && robot.state.error_code === 1001)
  ));
}

function updateSelectionPresentation() {
  const robots = configuredRobots();
  const onlineSelected = selectedRobots().filter((robot) => robot.connected).length;
  document.querySelectorAll('[data-robot-id]').forEach((card) => {
    const selected = selectedRobotIds.has(card.dataset.robotId);
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-selected', String(selected));
  });
  ui.selectionSummary.textContent =
    '已选择 ' + selectedRobotIds.size + ' / ' + robots.length +
    ' · 在线 ' + onlineSelected;
  ui.selectAllButton.disabled =
    current.busy || robots.length === 0 || selectedRobotIds.size === robots.length;
  ui.clearSelectionButton.disabled = current.busy || selectedRobotIds.size === 0;
  updateControlLocks();
}

function setSelection(robotIds) {
  const configured = new Set(configuredRobots().map((robot) => robot.id));
  selectedRobotIds = new Set(
    [...robotIds].filter((robotId) => configured.has(robotId)),
  );
  updateSelectionPresentation();
}

function toggleRobotSelection(robotId, additive) {
  if (additive) {
    const next = new Set(selectedRobotIds);
    if (next.has(robotId)) next.delete(robotId);
    else next.add(robotId);
    setSelection(next);
  } else {
    setSelection([robotId]);
  }
}

function robotStateDetail(robot) {
  const identity =
    (robot.target_ip || 'IP 自动发现') + ' · SN …' +
    (robot.serial_suffix || '未配置');
  const mode = robot.motion_mode || '模式未检测';
  const missing = robot.missing?.length
    ? ' · 缺少 ' + robot.missing.join(' / ')
    : '';
  const connectionError = robot.connect_error?.message
    ? ' · ' + robot.connect_error.message
    : '';
  if (!robot.state) return identity + ' · ' + mode + missing + connectionError;
  const state = robot.state;
  const velocity = Array.isArray(state.velocity)
    ? state.velocity.map((value) => Number(value).toFixed(3)).join(' / ')
    : '--';
  return identity + ' · ' + mode +
    ' · 电量 ' + Number(state.battery_soc).toFixed(0) + '%' +
    ' · mode ' + state.mode + ' · v ' + velocity +
    ' · yaw ' + Number(state.yaw_speed).toFixed(3) + missing + connectionError;
}

function robotConnectionLabel(robot) {
  if (robot.connected) return '在线';
  if (!robot.credentials_ready) return '配置不完整';
  if (robot.connect_error) return '连接失败';
  return '待连接';
}

function installRobotCardSelection(card) {
  card.addEventListener('click', (event) => {
    if (current.busy) return;
    toggleRobotSelection(
      card.dataset.robotId,
      event.shiftKey || event.ctrlKey || event.metaKey,
    );
  });
  card.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key) || current.busy) return;
    event.preventDefault();
    toggleRobotSelection(
      card.dataset.robotId,
      event.shiftKey || event.ctrlKey || event.metaKey,
    );
  });
}

function renderFleet(fleet) {
  if (!fleet || marqueeDrag) return;
  current.fleet = fleet;
  const configuredIds = (fleet.robots || []).map((robot) => robot.id);
  if (!selectionInitialized && configuredIds.length) {
    const initialIds = preferences.initialSelection === 'none'
      ? []
      : preferences.initialSelection === 'online'
        ? fleet.robots.filter((robot) => robot.connected).map((robot) => robot.id)
        : configuredIds;
    selectedRobotIds = new Set(initialIds);
    selectionInitialized = true;
  } else {
    selectedRobotIds = new Set(
      [...selectedRobotIds].filter((robotId) => configuredIds.includes(robotId)),
    );
  }

  const renderedIds = [...ui.fleetRobots.querySelectorAll('[data-robot-id]')]
    .map((card) => card.dataset.robotId);
  const topologyChanged = renderedIds.join('|') !== configuredIds.join('|');
  if (topologyChanged) {
    ui.fleetRobots.innerHTML = '';
    (fleet.robots || []).forEach((robot, index) => {
    const card = document.createElement('article');
    card.className = 'robot-card';
    card.dataset.robotId = robot.id;
    card.setAttribute('role', 'option');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-posinset', String(index + 1));
    card.setAttribute('aria-setsize', String(fleet.robots.length));

    const head = document.createElement('div');
    head.className = 'robot-card-head';
    const label = document.createElement('b');
    label.textContent = robot.label;
    const marker = document.createElement('span');
    marker.className = 'robot-status ' + (robot.connected ? 'online' : 'offline');
    marker.textContent = robotConnectionLabel(robot);
    head.append(label, marker);

    const id = document.createElement('span');
    id.className = 'robot-id';
    id.textContent = robot.id;
    const detail = document.createElement('small');
    detail.textContent = robotStateDetail(robot);
    card.append(head, id, detail);
    installRobotCardSelection(card);
      ui.fleetRobots.appendChild(card);
    });
  } else {
    (fleet.robots || []).forEach((robot) => {
      const card = ui.fleetRobots.querySelector(
        `[data-robot-id="${robot.id}"]`,
      );
      const marker = card.querySelector('.robot-status');
      marker.className = `robot-status ${robot.connected ? 'online' : 'offline'}`;
      marker.textContent = robotConnectionLabel(robot);
      card.querySelector('small').textContent = robotStateDetail(robot);
    });
  }

  updateSelectionPresentation();
}

function riskLabel(risk) { return ({ low: '低', medium: '中', high: '高', extreme: '极高' })[risk] || risk; }

function installHold(button, duration, callback) {
  let timer = null;
  const cancel = () => { if (timer) window.clearTimeout(timer); timer = null; button.classList.remove('holding'); };
  const start = (event) => {
    if (button.disabled || timer) return;
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
    event.preventDefault(); button.style.setProperty('--hold-duration', `${duration}ms`); button.classList.add('holding');
    timer = window.setTimeout(() => { timer = null; button.classList.remove('holding'); callback(); }, duration);
  };
  const end = (event) => { if (event.type === 'keyup' && !['Enter', ' '].includes(event.key)) return; cancel(); };
  button.addEventListener('pointerdown', start); button.addEventListener('pointerup', end);
  button.addEventListener('pointercancel', end); button.addEventListener('pointerleave', end);
  button.addEventListener('keydown', start); button.addEventListener('keyup', end);
  button.addEventListener('click', (event) => event.preventDefault());
}

function ensureLibrary(actions) {
  const signature = actions.map((item) => `${item.id}:${item.available}:${item.unavailable_reason || ''}`).join('|');
  if (signature === librarySignature) return;
  librarySignature = signature; ui.actionLibrary.innerHTML = ''; ui.editorAction.innerHTML = '';
  actions.forEach((action) => {
    if (action.editor_allowed) {
      const option = document.createElement('option'); option.value = action.id; option.textContent = action.label; ui.editorAction.appendChild(option);
    }
    const card = document.createElement('article');
    card.className = 'action-card'; card.dataset.category = action.category; card.dataset.risk = action.risk;
    const header = document.createElement('div'); header.className = 'action-card-head';
    const title = document.createElement('b'); title.textContent = action.label;
    const risk = document.createElement('span'); risk.className = `risk ${action.risk}`; risk.textContent = riskLabel(action.risk);
    header.append(title, risk);
    const verification = action.verified === 'real_robot' ? '本机已验收' : action.verified === 'real_robot_unsupported' ? '本机确认不支持' : '本机未验收';
    const meta = document.createElement('small'); meta.textContent = `API ${action.api_id} · ${verification}${action.unavailable_reason ? ` · ${action.unavailable_reason}` : ''}`;
    const result = document.createElement('div'); result.className = 'result-badge'; result.dataset.resultFor = action.id; result.textContent = '本次会话：未尝试';
    const button = document.createElement('button'); button.className = 'library-action-button hold-button'; button.dataset.libraryAction = action.id;
    button.innerHTML = '<span class="hold-fill"></span><span class="hold-copy"><b>执行到选中对象</b><small>按住确认</small></span>';
    installHold(button, action.hold_ms, () => executeLibraryAction(action));
    card.append(header, meta, result, button); ui.actionLibrary.appendChild(card);
  });
  applyFilter(); updateControlLocks();
}

function updateActionResults() {
  document.querySelectorAll('[data-result-for]').forEach((badge) => {
    const result = current.action_results?.[badge.dataset.resultFor];
    badge.className = `result-badge ${result?.status || ''}`;
    if (!result) { badge.textContent = '本次会话：未尝试'; return; }
    const labels = { accepted: '已接受', accepted_no_effect: '已接受但姿态未变化', acknowledged: '已应答（无状态码）', rejected: '机器狗拒绝', unsupported: '当前服务未实现（3203）', invalid_response: '回包无法识别', response_timeout: '已发送，等待回包超时', no_response: '未收到回包' };
    const code = result.code === null || result.code === undefined ? '' : ` · code ${result.code}`;
    const responseTime = result.response_ms === null || result.response_ms === undefined ? '' : ` · ${result.response_ms} ms`;
    const targets = Array.isArray(result.robots) ? ` · ${result.robots.length} 个对象` : '';
    badge.textContent = `本次会话：${labels[result.status] || result.status}${code}${responseTime}${targets} · ${result.updated_at}`;
  });
}

function applyFilter() {
  document.querySelectorAll('.action-card').forEach((card) => { card.hidden = activeFilter !== 'all' && card.dataset.category !== activeFilter; });
}

function updateControlLocks() {
  const selectedReady = selectionIsOnline();
  const ready = selectedReady && !current.busy && ui.clearanceCheck.checked;
  const controllerReady = actionControllerReady();
  document.querySelectorAll('[data-posture]').forEach((button) => {
    button.disabled = !ready;
    button.title = selectedReady ? '' : '请先选择已在线的机器狗。';
  });
  document.querySelectorAll('[data-library-action]').forEach((button) => {
    const action = current.actions.find(
      (item) => item.id === button.dataset.libraryAction,
    );
    const advancedOk =
      !action?.requires_advanced_ack ||
      ui.highRiskPhrase.value.trim() === 'GO2 HIGH RISK';
    const postureAction = ['stand_up', 'stand_down'].includes(action?.id);
    button.disabled =
      !ready ||
      !advancedOk ||
      action?.available === false ||
      (!postureAction && !controllerReady);
    button.title = action?.available === false
      ? action.unavailable_reason
      : !selectedReady
        ? '请先选择已在线的机器狗。'
        : !postureAction && !controllerReady
          ? '全部选中对象必须先起立。'
          : '';
  });
  ui.runChoreography.disabled =
    !ready || !controllerReady || timeline.length === 0;
  ui.runChoreography.title = !selectedReady
    ? '请先选择已在线的机器狗。'
    : controllerReady ? '' : '全部选中对象必须先起立。';
  ui.stopButton.disabled = current.busy || !selectedReady;
}

function render(status) {
  current = { ...current, ...status };
  const fleet = current.fleet || {};
  const connectedCount = fleet.connected_count || 0;
  const configuredCount = fleet.configured_count || 0;
  const anyConnected = connectedCount > 0;
  ui.connectionBadge.className = 'badge ' + (anyConnected ? 'online' : 'offline');
  ui.connectionBadge.textContent = anyConnected
    ? connectedCount + ' 台在线'
    : '未连接';
  ui.connectionTitle.textContent = current.busy
    ? '正在' + (current.operation || '执行操作') + '…'
    : anyConnected
      ? connectedCount + ' / ' + configuredCount + ' 台 WebRTC 已就绪'
      : '机器狗当前未连接';
  ui.connectionDetail.textContent = anyConnected
    ? '动作目标由 RTS 编队选择统一控制；多目标指令并发下发并分别校验。'
    : '页面只在本机运行；连接后由编队选择决定每次动作的执行对象。';
  ensureLibrary(current.actions || []);
  updateActionResults();
  renderFleet(fleet);
  updateControlLocks();
}

async function perform(label, path, body = {}) {
  render({ busy: true, operation: label });
  log('开始' + label + '。');
  try {
    const result = await api(path, { method: 'POST', body });
    if (result.warning) log(result.warning, 'warning');
    const targetCount = Array.isArray(result.robot_ids)
      ? '，对象 ' + result.robot_ids.length + ' 台'
      : '';
    const detail = result.completed
      ? '，完成 ' + result.completed.length + ' 步'
      : '';
    log(label + '完成' + targetCount + detail + '。', 'success');
  } catch (error) {
    log(label + '失败：' + error.message, 'error');
  } finally {
    ui.clearanceCheck.checked = false;
    ui.highRiskPhrase.value = '';
    try {
      render(await api('/api/status'));
    } catch (error) {
      render({ busy: false });
      log('状态同步失败：' + error.message, 'error');
    }
  }
}

function executeLibraryAction(action) {
  perform(action.label, '/api/library/' + action.id, {
    confirm_clearance: true,
    risk_ack: action.requires_advanced_ack
      ? ui.highRiskPhrase.value.trim()
      : '',
    robot_ids: selectedTargets(),
  });
}

function timelineSeconds() { return timeline.reduce((sum, step) => sum + Number(step.duration), 0); }

function customStepLabel(step) {
  if (step.kind === 'euler') return `自定义姿态 · R ${Number(step.roll).toFixed(2)} / P ${Number(step.pitch).toFixed(2)} / Y ${Number(step.yaw).toFixed(2)}`;
  if (step.kind === 'wait') return '停顿';
  return step.kind || '未知自定义动作';
}

function renderTimeline() {
  ui.timeline.innerHTML = '';
  timeline.forEach((step, index) => {
    const action = current.actions.find((item) => item.id === step.action);
    const item = document.createElement('li');
    const number = document.createElement('span'); number.className = 'step-number'; number.textContent = String(index + 1).padStart(2, '0');
    const copy = document.createElement('div'); copy.className = 'step-copy';
    const title = document.createElement('b'); title.textContent = step.action ? (action?.label || step.action) : customStepLabel(step);
    const meta = document.createElement('small'); meta.textContent = `${step.kind === 'wait' ? '等待' : '保持'} ${Number(step.duration).toFixed(1)} 秒`; copy.append(title, meta);
    const controls = document.createElement('div'); controls.className = 'step-controls';
    [['↑', -1], ['↓', 1]].forEach(([label, delta]) => {
      const button = document.createElement('button'); button.textContent = label; button.disabled = (delta === -1 && index === 0) || (delta === 1 && index === timeline.length - 1);
      button.addEventListener('click', () => { const other = index + delta; [timeline[index], timeline[other]] = [timeline[other], timeline[index]]; renderTimeline(); }); controls.appendChild(button);
    });
    const remove = document.createElement('button'); remove.textContent = '×'; remove.className = 'remove';
    remove.addEventListener('click', () => { timeline.splice(index, 1); renderTimeline(); }); controls.appendChild(remove);
    item.append(number, copy, controls); ui.timeline.appendChild(item);
  });
  ui.timelineTotal.textContent = `${timeline.length} 步 · ${timelineSeconds().toFixed(1)} 秒`; updateControlLocks();
}

function loadFeatured() {
  if (!current.featured_choreography) return;
  timeline = current.featured_choreography.steps.map((step) => ({ ...step }));
  ui.featuredDescription.textContent = current.featured_choreography.description;
  renderTimeline(); log(`已载入自定义编舞“${current.featured_choreography.name}”。`, 'success');
}

function marqueeRectangle(startX, startY, currentX, currentY) {
  return {
    left: Math.min(startX, currentX),
    top: Math.min(startY, currentY),
    right: Math.max(startX, currentX),
    bottom: Math.max(startY, currentY),
  };
}

function positionMarquee(rectangle) {
  const boardRect = ui.selectionBoard.getBoundingClientRect();
  ui.selectionMarquee.style.left = rectangle.left - boardRect.left + 'px';
  ui.selectionMarquee.style.top = rectangle.top - boardRect.top + 'px';
  ui.selectionMarquee.style.width =
    rectangle.right - rectangle.left + 'px';
  ui.selectionMarquee.style.height =
    rectangle.bottom - rectangle.top + 'px';
}

function finishMarquee(event, cancelled = false) {
  if (!marqueeDrag || event.pointerId !== marqueeDrag.pointerId) return;
  const drag = marqueeDrag;
  marqueeDrag = null;
  ui.selectionMarquee.hidden = true;
  if (ui.selectionBoard.hasPointerCapture(event.pointerId)) {
    ui.selectionBoard.releasePointerCapture(event.pointerId);
  }
  if (cancelled) return;
  const rect = marqueeRectangle(
    drag.startX,
    drag.startY,
    event.clientX,
    event.clientY,
  );
  if (rect.right - rect.left < 4 && rect.bottom - rect.top < 4) return;
  const hits = [...document.querySelectorAll('[data-robot-id]')]
    .filter((card) => {
      const cardRect = card.getBoundingClientRect();
      return !(
        cardRect.right < rect.left ||
        cardRect.left > rect.right ||
        cardRect.bottom < rect.top ||
        cardRect.top > rect.bottom
      );
    })
    .map((card) => card.dataset.robotId);
  setSelection(drag.additive ? new Set([...drag.initial, ...hits]) : hits);
}

ui.selectionBoard.addEventListener('pointerdown', (event) => {
  if (
    current.busy ||
    event.button !== 0 ||
    event.target.closest('[data-robot-id]')
  ) return;
  event.preventDefault();
  marqueeDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    additive: event.shiftKey || event.ctrlKey || event.metaKey,
    initial: new Set(selectedRobotIds),
  };
  ui.selectionBoard.setPointerCapture(event.pointerId);
  ui.selectionMarquee.hidden = false;
  positionMarquee(
    marqueeRectangle(event.clientX, event.clientY, event.clientX, event.clientY),
  );
});
ui.selectionBoard.addEventListener('pointermove', (event) => {
  if (!marqueeDrag || event.pointerId !== marqueeDrag.pointerId) return;
  positionMarquee(
    marqueeRectangle(
      marqueeDrag.startX,
      marqueeDrag.startY,
      event.clientX,
      event.clientY,
    ),
  );
});
ui.selectionBoard.addEventListener('pointerup', (event) => {
  finishMarquee(event);
});
ui.selectionBoard.addEventListener('pointercancel', (event) => {
  finishMarquee(event, true);
});

ui.selectAllButton.addEventListener('click', () => {
  setSelection(configuredRobots().map((robot) => robot.id));
});
ui.clearSelectionButton.addEventListener('click', () => {
  setSelection([]);
});
ui.stopButton.addEventListener('click', () => {
  perform('停止选中对象', '/api/stop-move', {
    robot_ids: selectedTargets(),
  });
});
ui.clearActivity.addEventListener('click', () => {
  ui.activityList.innerHTML = '';
});
ui.clearanceCheck.addEventListener('change', updateControlLocks);
ui.highRiskPhrase.addEventListener('input', updateControlLocks);
document.querySelectorAll('[data-posture]').forEach((button) => {
  installHold(button, 1200, () => {
    const action = button.dataset.posture;
    perform(
      action === 'stand_up' ? '起立' : '趴下',
      '/api/actions/' + action,
      {
        confirm_clearance: true,
        robot_ids: selectedTargets(),
      },
    );
  });
});
installHold(ui.runChoreography, 1200, () => {
  perform('自定义编舞', '/api/choreographies/run', {
    confirm_clearance: true,
    steps: timeline,
    robot_ids: selectedTargets(),
  });
});

ui.libraryFilters.addEventListener('click', (event) => {
  const button = event.target.closest('[data-filter]'); if (!button) return;
  activeFilter = button.dataset.filter; document.querySelectorAll('.filter').forEach((item) => item.classList.toggle('active', item === button)); applyFilter();
});
ui.addStep.addEventListener('click', () => {
  const duration = Number(ui.editorDuration.value);
  if (timeline.length >= 12) return log('编舞最多 12 步。', 'warning');
  if (!Number.isFinite(duration) || duration < 0.5 || duration > 8) return log('等待时间必须在 0.5 到 8 秒之间。', 'warning');
  if (timelineSeconds() + duration > 40) return log('编舞总时长不能超过 40 秒。', 'warning');
  timeline.push({ action: ui.editorAction.value, duration }); renderTimeline();
});
function canAppendDuration(duration) {
  if (timeline.length >= 12) { log('编舞最多 12 步。', 'warning'); return false; }
  if (!Number.isFinite(duration) || duration < 0.5 || duration > 8) { log('保持时间必须在 0.5 到 8 秒之间。', 'warning'); return false; }
  if (timelineSeconds() + duration > 40) { log('编舞总时长不能超过 40 秒。', 'warning'); return false; }
  return true;
}
function updateCustomFields() {
  const kind = ui.customKind.value;
  ui.customEulerFields.hidden = kind !== 'euler';
}
ui.customKind.addEventListener('change', updateCustomFields);
ui.addCustomStep.addEventListener('click', () => {
  const kind = ui.customKind.value;
  const duration = Number(ui.customDuration.value);
  if (!canAppendDuration(duration)) return;
  let step = { kind, duration };
  if (kind === 'euler') {
    const values = [Number(ui.customRoll.value), Number(ui.customPitch.value), Number(ui.customYaw.value)];
    if (!values.every(Number.isFinite) || values[0] < -0.12 || values[0] > 0.12 || values[1] < -0.20 || values[1] > 0.20 || values[2] < -0.30 || values[2] > 0.30) return log('姿态参数超出面板保守范围。', 'warning');
    step = { kind, roll: values[0], pitch: values[1], yaw: values[2], duration };
  }
  timeline.push(step); renderTimeline(); log(`已加入${customStepLabel(step)}。`, 'success');
});
updateCustomFields();
ui.loadFeatured.addEventListener('click', loadFeatured); ui.clearTimeline.addEventListener('click', () => { timeline = []; renderTimeline(); });
ui.saveDraft.addEventListener('click', () => { localStorage.setItem(draftStorageKey, JSON.stringify(timeline)); log('编舞草稿已保存在本机浏览器。', 'success'); });
ui.loadDraft.addEventListener('click', () => {
  try { const draft = JSON.parse(localStorage.getItem(draftStorageKey) || '[]'); if (!Array.isArray(draft)) throw new Error(); timeline = draft; renderTimeline(); log('已读取本机编舞草稿。', 'success'); }
  catch { log('草稿格式无效，未载入。', 'error'); }
});

async function poll() {
  try { const status = await api('/api/status'); render(status); if (!featuredAutoLoaded && timeline.length === 0 && status.featured_choreography) { featuredAutoLoaded = true; loadFeatured(); } await maybeAutoConnect(); }
  catch (error) { log(`无法读取面板状态：${error.message}`, 'error'); }
}

async function maybeAutoConnect() {
  const fleet = current.fleet || {};
  if (
    !preferences.autoConnect || current.busy || autoConnectInFlight ||
    fleet.connected || !fleet.ready_to_connect || Date.now() < nextAutoConnectAt
  ) return;
  autoConnectInFlight = true;
  nextAutoConnectAt = Date.now() + 10000;
  render({ busy: true, operation: '自动发现并连接设备' });
  log('检测到离线设备，正在自动发现当前 WiFi 地址。');
  try {
    const result = await api('/api/fleet/connect', { method: 'POST', body: {} });
    render(result);
    if (result.warning) log(result.warning, 'warning');
    else log('全部设备已自动连接。', 'success');
  } catch (error) {
    log(`自动连接暂未完成：${error.message}`, 'warning');
  } finally {
    autoConnectInFlight = false;
    try { render(await api('/api/status')); } catch { render({ busy: false }); }
  }
}

poll(); window.setInterval(() => {
  if (!current.busy && !marqueeDrag) poll();
}, preferences.pollIntervalMs);
