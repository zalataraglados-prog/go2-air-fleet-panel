const token = document.querySelector('meta[name="go2-panel-token"]').content;
const preferencesStorageKey = 'go2_panel_preferences_v1';
const defaultPreferences = {
  autoConnect: true,
  initialSelection: 'all',
  pollIntervalMs: 2000,
  compactCards: false,
};

const ui = Object.fromEntries([
  'settingsConnectionBadge', 'settingsConnectionTitle',
  'settingsConnectionDetail', 'settingsFleetCount', 'settingsRobotGrid',
  'settingsConnectButton', 'settingsRefreshButton',
  'settingsDisconnectButton', 'preferencesForm', 'initialSelection',
  'pollIntervalMs', 'autoConnect', 'compactCards', 'resetPreferencesButton',
  'settingsActivityList', 'clearSettingsActivity',
].map((id) => [id, document.querySelector(`#${id}`)]));

let current = { busy: false, fleet: { robots: [], configured_count: 0, connected_count: 0 } };
let autoConnectInFlight = false;
let nextAutoConnectAt = 0;

function now() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function log(message, kind = '') {
  const item = document.createElement('li');
  if (kind) item.className = kind;
  const time = document.createElement('time');
  time.textContent = now();
  const copy = document.createElement('span');
  copy.textContent = message;
  item.append(time, copy);
  ui.settingsActivityList.prepend(item);
  while (ui.settingsActivityList.children.length > 20) {
    ui.settingsActivityList.lastElementChild.remove();
  }
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

function robotDetail(robot) {
  const identity = `${robot.target_ip || 'IP 自动发现'} · SN …${robot.serial_suffix || '未配置'}`;
  if (robot.state) {
    return `${identity} · 电量 ${Number(robot.state.battery_soc).toFixed(0)}% · ${robot.motion_mode || '模式未检测'}`;
  }
  if (robot.connect_error?.message) return `${identity} · ${robot.connect_error.message}`;
  if (robot.missing?.length) return `${identity} · 缺少 ${robot.missing.join(' / ')}`;
  return `${identity} · 等待连接`;
}

function renderRobots(robots) {
  ui.settingsRobotGrid.innerHTML = '';
  robots.forEach((robot) => {
    const card = document.createElement('article');
    card.className = `settings-robot-card ${robot.connected ? 'online' : ''}`;
    const head = document.createElement('div');
    head.className = 'settings-robot-head';
    const label = document.createElement('b');
    label.textContent = robot.label;
    const badge = document.createElement('span');
    badge.className = `robot-status ${robot.connected ? 'online' : 'offline'}`;
    badge.textContent = robot.connected
      ? '在线'
      : robot.connect_error
        ? '连接失败'
        : robot.credentials_ready
          ? '待连接'
          : '配置不完整';
    head.append(label, badge);
    const id = document.createElement('code');
    id.textContent = robot.id;
    const detail = document.createElement('small');
    detail.textContent = robotDetail(robot);
    card.append(head, id, detail);
    ui.settingsRobotGrid.appendChild(card);
  });
}

function render(status) {
  current = { ...current, ...status };
  const fleet = current.fleet || {};
  const configured = Number(fleet.configured_count || 0);
  const connected = Number(fleet.connected_count || 0);
  const complete = configured > 0 && connected === configured;
  ui.settingsConnectionBadge.className = `badge ${connected ? 'online' : 'offline'}`;
  ui.settingsConnectionBadge.textContent = connected ? `${connected} 台在线` : '未连接';
  ui.settingsConnectionTitle.textContent = current.busy
    ? `正在${current.operation || '处理连接'}…`
    : complete
      ? `${connected} / ${configured} 台已全部就绪`
      : `${connected} / ${configured} 台在线`;
  ui.settingsConnectionDetail.textContent = complete
    ? '六机 WebRTC、DataChannel 与只读状态均已建立。'
    : '可重试离线设备；已在线会话不会被重复连接或主动断开。';
  ui.settingsFleetCount.textContent = `${connected} / ${configured} 在线`;
  renderRobots(fleet.robots || []);
  ui.settingsConnectButton.disabled = current.busy || !fleet.ready_to_connect || complete;
  ui.settingsRefreshButton.disabled = current.busy || connected === 0;
  ui.settingsDisconnectButton.disabled = current.busy || connected === 0;
}

async function perform(label, path) {
  render({ busy: true, operation: label });
  log(`开始${label}。`);
  try {
    const result = await api(path, { method: 'POST', body: {} });
    render(result);
    if (result.warning) log(result.warning, 'warning');
    else log(`${label}完成。`, 'success');
  } catch (error) {
    log(`${label}失败：${error.message}`, 'error');
  } finally {
    try {
      render(await api('/api/status'));
    } catch (error) {
      render({ busy: false });
      log(`状态同步失败：${error.message}`, 'error');
    }
  }
}

function loadPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(preferencesStorageKey) || '{}');
    ui.autoConnect.checked = saved.autoConnect !== false;
    ui.initialSelection.value = ['all', 'online', 'none'].includes(saved.initialSelection)
      ? saved.initialSelection
      : defaultPreferences.initialSelection;
    ui.pollIntervalMs.value = [1000, 2000, 5000].includes(Number(saved.pollIntervalMs))
      ? String(saved.pollIntervalMs)
      : String(defaultPreferences.pollIntervalMs);
    ui.compactCards.checked = saved.compactCards === true;
  } catch {
    setPreferenceFields(defaultPreferences);
  }
}

function setPreferenceFields(preferences) {
  ui.autoConnect.checked = preferences.autoConnect;
  ui.initialSelection.value = preferences.initialSelection;
  ui.pollIntervalMs.value = String(preferences.pollIntervalMs);
  ui.compactCards.checked = preferences.compactCards;
}

ui.settingsConnectButton.addEventListener('click', () => {
  perform('连接 / 重试离线设备', '/api/fleet/connect');
});
ui.settingsRefreshButton.addEventListener('click', () => {
  perform('刷新只读状态', '/api/fleet/state/refresh');
});
ui.settingsDisconnectButton.addEventListener('click', () => {
  ui.autoConnect.checked = false;
  persistPreferences();
  log('自动连接已关闭，避免主动断开后再次连接。', 'warning');
  perform('断开全部连接', '/api/disconnect');
});
function currentPreferences() {
  return {
    autoConnect: ui.autoConnect.checked,
    initialSelection: ui.initialSelection.value,
    pollIntervalMs: Number(ui.pollIntervalMs.value),
    compactCards: ui.compactCards.checked,
  };
}
function persistPreferences() {
  localStorage.setItem(
    preferencesStorageKey,
    JSON.stringify(currentPreferences()),
  );
}
ui.preferencesForm.addEventListener('submit', (event) => {
  event.preventDefault();
  persistPreferences();
  log('动作台偏好已保存，下次打开或刷新动作台时生效。', 'success');
});
ui.resetPreferencesButton.addEventListener('click', () => {
  setPreferenceFields(defaultPreferences);
  localStorage.removeItem(preferencesStorageKey);
  log('动作台偏好已恢复默认。', 'success');
});
ui.clearSettingsActivity.addEventListener('click', () => {
  ui.settingsActivityList.innerHTML = '';
});

async function poll() {
  if (current.busy) return;
  try {
    render(await api('/api/status'));
    await maybeAutoConnect();
  } catch (error) {
    log(`无法读取面板状态：${error.message}`, 'error');
  }
}

async function maybeAutoConnect() {
  const fleet = current.fleet || {};
  if (
    !ui.autoConnect.checked || current.busy || autoConnectInFlight ||
    fleet.connected || !fleet.ready_to_connect || Date.now() < nextAutoConnectAt
  ) return;
  autoConnectInFlight = true;
  nextAutoConnectAt = Date.now() + 10000;
  try {
    await perform('自动发现并连接设备', '/api/fleet/connect');
  } finally {
    autoConnectInFlight = false;
  }
}

loadPreferences();
poll();
window.setInterval(poll, 2000);
