import { invoke } from '@tauri-apps/api/core';
import { WebviewWindow } from '@tauri-apps/api/webviewWindow';

const el = {
  hostUsername: document.getElementById('host-username'),
  hostPassword: document.getElementById('host-password'),
  toggleHostPassword: document.getElementById('toggle-host-password'),
  setHostUsername: document.getElementById('set-host-username'),
  setHostPassword: document.getElementById('set-host-password'),

  portInput: document.getElementById('port-input'),
  checkPort: document.getElementById('check-port'),
  setPort: document.getElementById('set-port'),

  fpsInput: document.getElementById('fps-input'),
  setFps: document.getElementById('set-fps'),
  setScale: document.getElementById('set-scale'),

  startHost: document.getElementById('start-host'),
  stopHost: document.getElementById('stop-host'),

  connectUsername: document.getElementById('connect-username'),
  connectPassword: document.getElementById('connect-password'),
  connectUrl: document.getElementById('connect-url'),
  connectHost: document.getElementById('connect-host'),

  settingsToggle: document.getElementById('settings-toggle'),
  settingsPopover: document.getElementById('settings-popover'),
  autoRefresh: document.getElementById('auto-refresh'),

  statusPill: document.getElementById('status-pill'),
  statusLabel: document.getElementById('status-label'),

  scaleDropdown: document.getElementById('scale-dropdown'),
  monitorDropdown: document.getElementById('monitor-dropdown'),
};

const state = {
  busy: false,
  running: false,
  autoRefresh: true,
  monitorIndex: 1,
  scale: 100,
  hostUsername: 'admin',
  hostPassword: 'warpdesk',
  connectUsername: 'admin',
  connectPassword: 'warpdesk',
  connectUrl: 'https://127.0.0.1:8443',
  monitorOptions: [{ value: 1, label: 'Monitor 1' }],
  transientUntil: 0,
};

const dropdowns = new Map();
let transientTimer = null;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function normalizeUrl(raw) {
  let url = String(raw || '').trim();
  if (!url) return '';
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = `https://${url}`;
  }
  if (url.endsWith('/')) {
    url = url.slice(0, -1);
  }
  return url;
}

function setStatus(stateClass, label, text) {
  el.statusPill.classList.remove('status-green', 'status-yellow', 'status-red', 'status-grey');
  el.statusPill.classList.add(stateClass);
  el.statusLabel.textContent = label;
}

function setTransientStatus(stateClass, label, ms = 3000) {
  state.transientUntil = Date.now() + ms;
  setStatus(stateClass, label);
  if (transientTimer) {
    window.clearTimeout(transientTimer);
  }
  transientTimer = window.setTimeout(() => {
    state.transientUntil = 0;
    transientTimer = null;
    void refreshBackendState(true);
  }, ms);
}

function shortStatus(text, fallback = 'Error') {
  const raw = String(text || '').replace(/\s+/g, ' ').trim();
  if (!raw) return fallback;
  return raw.length > 48 ? `${raw.slice(0, 47)}...` : raw;
}

function bindDropdown(root, options, initialValue, onChange) {
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'dd-trigger';

  const valueText = document.createElement('span');
  const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  chevron.setAttribute('viewBox', '0 0 12 12');
  chevron.classList.add('dd-chevron');
  chevron.innerHTML = '<path d="M2 4.5L6 8.5L10 4.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>';

  const panel = document.createElement('div');
  panel.className = 'dd-panel';

  root.innerHTML = '';
  trigger.appendChild(valueText);
  root.appendChild(trigger);
  root.appendChild(chevron);
  root.appendChild(panel);

  const model = {
    value: String(initialValue),
    options,
    root,
    trigger,
    panel,
    valueText,
    onChange,
  };

  options.forEach((opt) => {
    const node = document.createElement('div');
    node.className = 'dd-option';
    node.dataset.value = String(opt.value);
    node.textContent = opt.label;
    node.addEventListener('click', () => {
      setDropdownValue(model, String(opt.value), true);
      closeDropdown(model);
    });
    panel.appendChild(node);
  });

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleDropdown(model);
  });

  dropdowns.set(root.id, model);
  setDropdownValue(model, String(initialValue), false);
}

function setDropdownValue(model, value, shouldNotify) {
  model.value = value;
  const selected = model.options.find((o) => String(o.value) === String(value)) || model.options[0];
  model.valueText.textContent = selected ? selected.label : '';
  model.panel.querySelectorAll('.dd-option').forEach((node) => {
    node.classList.toggle('selected', node.dataset.value === String(value));
  });
  if (shouldNotify && typeof model.onChange === 'function') {
    model.onChange(value);
  }
}

function openDropdown(model) {
  closeAllDropdowns();
  model.panel.classList.add('open');
  model.trigger.classList.add('open');
}

function closeDropdown(model) {
  model.panel.classList.remove('open');
  model.trigger.classList.remove('open');
}

function toggleDropdown(model) {
  if (model.panel.classList.contains('open')) {
    closeDropdown(model);
  } else {
    openDropdown(model);
  }
}

function closeAllDropdowns() {
  dropdowns.forEach((model) => closeDropdown(model));
}

function runtimeConfig() {
  const hostUrl = normalizeUrl(state.connectUrl) || `https://127.0.0.1:${state.port || '8443'}`;
  const maxMonitor = Math.max(1, Number(state.monitorOptions.length || 1));
  return {
    hostUrl,
    username: (state.hostUsername || 'admin').trim() || 'admin',
    password: state.hostPassword,
    fps: clamp(Number(state.fps || 60), 1, 120),
    scale: Number(state.scale || 100),
    monitorIndex: clamp(Number(state.monitorIndex || 1), 1, maxMonitor),
  };
}

async function refreshBackendState(force = false) {
  if (!force && Date.now() < state.transientUntil) {
    return;
  }
  try {
    const result = await invoke('backend_state');
    state.running = !!result.running;
    if (state.running) {
      setStatus('status-green', 'Running', `Backend active${result.pid ? ` (pid ${result.pid})` : ''}`);
      el.startHost.disabled = true;
      el.stopHost.disabled = false;
    } else {
      setStatus('status-grey', 'Not Started', 'Ready');
      el.startHost.disabled = false;
      el.stopHost.disabled = true;
    }
  } catch (err) {
    setStatus('status-red', 'Error', `State check failed: ${String(err)}`);
  }
}

async function startHost() {
  if (state.busy) return;
  state.busy = true;
  setStatus('status-yellow', 'Starting');
  try {
    await invoke('start_backend', { config: runtimeConfig() });
    await refreshBackendState(true);
    if (state.running) {
      setTransientStatus('status-green', 'Host Started');
    }
  } catch (err) {
    const text = String(err || 'Unknown error');
    if (/admin|administrator|uac|elevat/i.test(text)) {
      setTransientStatus('status-red', 'Run As Admin');
    } else {
      setTransientStatus('status-red', shortStatus(text, 'Start Failed'));
    }
  } finally {
    state.busy = false;
  }
}

async function stopHost() {
  if (state.busy) return;
  state.busy = true;
  setStatus('status-yellow', 'Stopping');
  try {
    await invoke('stop_backend');
    await refreshBackendState(true);
    setTransientStatus('status-grey', 'Host Stopped');
  } catch (err) {
    setTransientStatus('status-red', 'Stop Failed');
  } finally {
    state.busy = false;
  }
}

async function checkPort() {
  const hostUrl = normalizeUrl(state.connectUrl) || `https://127.0.0.1:${state.port || '8443'}`;
  setStatus('status-yellow', 'Checking');
  try {
    const message = await invoke('check_port', { hostUrl });
    if (/reachable/i.test(String(message))) {
      setTransientStatus('status-green', 'Port Open');
    } else {
      setTransientStatus('status-red', 'Port Closed');
    }
  } catch (err) {
    setTransientStatus('status-red', 'Port Check Failed');
  }
}

async function connectRemote() {
  const hostUrl = normalizeUrl(state.connectUrl);
  if (!hostUrl || !state.connectPassword) {
    setTransientStatus('status-red', 'Missing Connection Data');
    return;
  }

  setStatus('status-yellow', 'Connecting');

  try {
    const result = await invoke('connect_remote', {
      hostUrl,
      username: state.connectUsername || 'admin',
      password: state.connectPassword,
    });

    if (!result.success) {
      setTransientStatus('status-red', 'Login Failed');
      return;
    }

    const desktopUrl = result.desktopUrl || `${hostUrl}/desktop.html`;
    const token = result.token || '';
    const username = state.connectUsername || 'admin';
    const esc = (v) => JSON.stringify(String(v));

    const initScript = `
      try {
        localStorage.setItem('warpdesk_host_url', ${esc(hostUrl)});
        localStorage.setItem('warpdesk_session_token', ${esc(token)});
        localStorage.setItem('warpdesk_username', ${esc(username)});
      } catch (_) {}
    `;

    const label = `remote-${Date.now()}`;
    new WebviewWindow(label, {
      url: desktopUrl,
      title: `WarpDesk Remote - ${hostUrl}`,
      width: 1280,
      height: 760,
      minWidth: 980,
      minHeight: 620,
      initializationScript: initScript,
    });

    setTransientStatus('status-green', 'Connected');
  } catch (err) {
    setTransientStatus('status-red', 'Connect Failed');
  }
}

async function loadMonitorOptions() {
  try {
    const monitors = await invoke('list_monitors');
    if (Array.isArray(monitors) && monitors.length) {
      state.monitorOptions = monitors.map((m, idx) => {
        const value = Number(m.index || idx + 1);
        const label = m.label || `Monitor ${value}`;
        return { value, label };
      });
      return;
    }
  } catch (_) {
    // Fallback below when monitor API is unavailable.
  }

  state.monitorOptions = [{ value: 1, label: 'Monitor 1' }];
}

function bindEvents() {
  el.toggleHostPassword.addEventListener('click', () => {
    const showing = el.hostPassword.type === 'text';
    el.hostPassword.type = showing ? 'password' : 'text';
    el.toggleHostPassword.textContent = showing ? 'Show' : 'Hide';
  });

  el.setHostUsername.addEventListener('click', () => {
    state.hostUsername = el.hostUsername.value.trim() || 'admin';
    state.connectUsername = state.hostUsername;
    el.connectUsername.value = state.connectUsername;
    setTransientStatus('status-grey', 'Username Updated');
  });

  el.setHostPassword.addEventListener('click', () => {
    state.hostPassword = el.hostPassword.value;
    state.connectPassword = state.hostPassword;
    el.connectPassword.value = state.connectPassword;
    setTransientStatus('status-grey', 'Password Updated');
  });

  el.setPort.addEventListener('click', () => {
    state.port = String(clamp(Number(el.portInput.value || 8443), 1, 65535));
    el.portInput.value = state.port;
    state.connectUrl = `https://127.0.0.1:${state.port}`;
    el.connectUrl.value = state.connectUrl;
    setTransientStatus('status-grey', `Port ${state.port}`);
  });

  el.checkPort.addEventListener('click', checkPort);

  el.setFps.addEventListener('click', () => {
    state.fps = String(clamp(Number(el.fpsInput.value || 60), 1, 120));
    el.fpsInput.value = state.fps;
    setTransientStatus('status-grey', `FPS ${state.fps}`);
  });

  el.setScale.addEventListener('click', () => {
    setTransientStatus('status-grey', `Scale ${state.scale}%`);
  });

  el.startHost.addEventListener('click', startHost);
  el.stopHost.addEventListener('click', stopHost);
  el.connectHost.addEventListener('click', connectRemote);

  el.connectUsername.addEventListener('input', () => {
    state.connectUsername = el.connectUsername.value;
  });
  el.connectPassword.addEventListener('input', () => {
    state.connectPassword = el.connectPassword.value;
  });
  el.connectUrl.addEventListener('input', () => {
    state.connectUrl = el.connectUrl.value;
  });

  el.settingsToggle.addEventListener('click', () => {
    el.settingsPopover.hidden = !el.settingsPopover.hidden;
  });

  document.addEventListener('click', (event) => {
    if (!el.settingsPopover.hidden) {
      const inSettings = el.settingsPopover.contains(event.target) || el.settingsToggle.contains(event.target);
      if (!inSettings) {
        el.settingsPopover.hidden = true;
      }
    }
    if (!event.target.closest('.custom-dropdown')) {
      closeAllDropdowns();
    }
  });

  el.autoRefresh.addEventListener('change', () => {
    state.autoRefresh = el.autoRefresh.checked;
  });
}

async function initDropdowns() {
  bindDropdown(
    el.scaleDropdown,
    [
      { value: 100, label: '100% (Native)' },
      { value: 85, label: '85% (Balanced)' },
      { value: 75, label: '75% (Scaled)' },
      { value: 50, label: '50% (Fast)' },
    ],
    100,
    (value) => {
      state.scale = Number(value);
    },
  );

  await loadMonitorOptions();

  bindDropdown(
    el.monitorDropdown,
    state.monitorOptions,
    1,
    (value) => {
      state.monitorIndex = Number(value);
      setTransientStatus('status-grey', `Monitor ${value}`);
    },
  );
}

async function bootstrap() {
  bindEvents();
  await initDropdowns();
  state.port = el.portInput.value;
  state.fps = el.fpsInput.value;

  await refreshBackendState();

  window.setInterval(() => {
    if (state.autoRefresh) {
      void refreshBackendState();
    }
  }, 3000);
}

void bootstrap();
