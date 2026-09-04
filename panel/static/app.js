const token = document.querySelector('meta[name="go2-panel-token"]').content;
const ui = {
  connectionBadge: document.querySelector('#connectionBadge'),
  connectionTitle: document.querySelector('#connectionTitle'),
  connectionDetail: document.querySelector('#connectionDetail'),
  connect: document.querySelector('#connectButton'),
  disconnect: document.querySelector('#disconnectButton'),
  refresh: document.querySelector('#refreshButton'),
  stop: document.querySelector('#stopButton'),
  clearance: document.querySelector('#clearanceCheck'),
  holds: [...document.querySelectorAll('.hold-button')],
  battery: document.querySelector('#batteryValue'),
  voltage: document.querySelector('#voltageValue'),
  rpy: document.querySelector('#rpyValue'),
  mode: document.querySelector('#modeValue'),
  velocity: document.querySelector('#velocityValue'),
  foot: document.querySelector('#footValue'),
  activity: document.querySelector('#activityList'),
  clearActivity: document.querySelector('#clearActivity'),
};

let current = { connected: false, busy: false, operation: null, state: null };

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
  ui.activity.prepend(item);
  while (ui.activity.children.length > 30) ui.activity.lastElementChild.remove();
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

function formatVector(values, digits = 2) {
  if (!Array.isArray(values)) return '--';
  return values.map((value) => Number(value).toFixed(digits)).join(' / ');
}

function renderState(state) {
  if (!state) {
    ui.battery.textContent = '--';
    ui.voltage.textContent = '等待状态';
    ui.rpy.textContent = '-- / -- / --';
    ui.mode.textContent = '--';
    ui.velocity.textContent = '速度 --';
    ui.foot.textContent = '--';
    return;
  }
  ui.battery.textContent = `${state.battery_soc.toFixed(0)}%`;
  ui.voltage.textContent = `${state.power_voltage.toFixed(2)} V`;
  ui.rpy.textContent = formatVector(state.rpy, 3);
  ui.mode.textContent = `模式 ${state.mode} · 步态 ${state.gait_type}`;
  ui.velocity.textContent = `速度 ${formatVector(state.velocity, 3)} · yaw ${state.yaw_speed.toFixed(3)}`;
  const totalForce = state.foot_force.reduce((sum, value) => sum + Math.max(value, 0), 0);
  ui.foot.textContent = totalForce.toFixed(0);
}

function render(status) {
  current = { ...current, ...status };
  const { connected, busy, operation } = current;
  ui.connectionBadge.className = `badge ${connected ? 'online' : 'offline'}`;
  ui.connectionBadge.textContent = connected ? '已连接' : '未连接';
  ui.connectionTitle.textContent = busy
    ? `正在${operation || '执行操作'}…`
    : connected ? 'WebRTC 与 DataChannel 已就绪' : '机器狗当前未连接';
  ui.connectionDetail.textContent = connected
    ? '保持单一常驻会话；页面不会保存设备密钥。'
    : '面板只在本机运行；点击连接后才会建立 WebRTC 会话。';
  ui.connect.disabled = busy || connected;
  ui.disconnect.disabled = busy || !connected;
  ui.refresh.disabled = busy || !connected;
  ui.stop.disabled = busy || !connected;
  const postureReady = connected && !busy && ui.clearance.checked;
  ui.holds.forEach((button) => { button.disabled = !postureReady; });
  renderState(current.state);
}

async function perform(label, path, body = {}) {
  render({ busy: true, operation: label });
  log(`开始${label}。`);
  try {
    const result = await api(path, { method: 'POST', body });
    if (result.state) current.state = result.state;
    if (result.warning) log(result.warning, 'warning');
    log(`${label}完成。`, 'success');
  } catch (error) {
    log(`${label}失败：${error.message}`, 'error');
  } finally {
    ui.clearance.checked = false;
    try {
      const status = await api('/api/status');
      render(status);
    } catch (error) {
      render({ busy: false });
      log(`状态同步失败：${error.message}`, 'error');
    }
  }
}

ui.connect.addEventListener('click', () => perform('连接机器狗', '/api/connect'));
ui.disconnect.addEventListener('click', () => perform('断开连接', '/api/disconnect'));
ui.refresh.addEventListener('click', () => perform('刷新状态', '/api/state/refresh'));
ui.stop.addEventListener('click', () => perform('StopMove', '/api/stop-move'));
ui.clearance.addEventListener('change', () => render({}));
ui.clearActivity.addEventListener('click', () => { ui.activity.innerHTML = ''; });

function installHold(button) {
  const holdMs = 1200;
  let timer = null;
  let fired = false;

  const cancel = () => {
    if (timer) window.clearTimeout(timer);
    timer = null;
    button.classList.remove('holding');
  };
  const start = (event) => {
    if (button.disabled || timer || fired) return;
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    fired = false;
    button.classList.add('holding');
    timer = window.setTimeout(() => {
      timer = null;
      fired = true;
      button.classList.remove('holding');
      const action = button.dataset.action;
      const label = action === 'stand_up' ? '起立' : '趴下';
      perform(label, `/api/actions/${action}`, { confirm_clearance: true });
    }, holdMs);
  };
  const end = (event) => {
    if (event.type === 'keyup' && !['Enter', ' '].includes(event.key)) return;
    cancel();
    window.setTimeout(() => { fired = false; }, 0);
  };
  button.addEventListener('pointerdown', start);
  button.addEventListener('pointerup', end);
  button.addEventListener('pointercancel', end);
  button.addEventListener('pointerleave', end);
  button.addEventListener('keydown', start);
  button.addEventListener('keyup', end);
  button.addEventListener('click', (event) => event.preventDefault());
}

ui.holds.forEach(installHold);

async function poll() {
  try {
    const status = await api('/api/status');
    render(status);
  } catch (error) {
    log(`无法读取面板状态：${error.message}`, 'error');
  }
}

poll();
window.setInterval(() => {
  if (!current.busy) poll();
}, 2000);
