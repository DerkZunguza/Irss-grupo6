// app.js — Lógica do frontend Hardware Monitor (ESP32-S3 / SIM900)
// PWA SPA com auto-refresh e suporte offline

'use strict';

/* ══════════════════════════════════════════════════════════════════════════════
   CONFIGURAÇÃO
   ══════════════════════════════════════════════════════════════════════════════ */

const CONFIG = {
  // URL base da API — em produção, servida pelo nginx na mesma origem
  apiBase: window.location.origin + '/api',

  // Intervalos de atualização automática (ms)
  dashboardRefresh: 30_000,
  sensorRefresh:    30_000,
  logsRefresh:      60_000,

  // Máximo de mensagens no SIM900
  sim900Max: 20,
};

// Estado global da aplicação
const state = {
  devices:        [],
  currentDeviceId: null,
  sensorChart:    null,
  timers:         {},
  smsCount:       0,   // número de SMS guardados no servidor para o dispositivo
  offline:        false,
};

/* ══════════════════════════════════════════════════════════════════════════════
   REGISTO DO SERVICE WORKER (PWA)
   ══════════════════════════════════════════════════════════════════════════════ */

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js')
      .then(reg => console.log('[PWA] Service worker registado:', reg.scope))
      .catch(err => console.warn('[PWA] Falha no registo:', err));
  });
}

/* ══════════════════════════════════════════════════════════════════════════════
   PWA INSTALL BANNER
   ══════════════════════════════════════════════════════════════════════════════ */

let deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  const banner = document.getElementById('install-banner');
  if (banner) banner.classList.add('visible');
});

function installPWA() {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  deferredInstallPrompt.userChoice.then(() => {
    deferredInstallPrompt = null;
    const banner = document.getElementById('install-banner');
    if (banner) banner.classList.remove('visible');
  });
}

function dismissInstallBanner() {
  const banner = document.getElementById('install-banner');
  if (banner) banner.classList.remove('visible');
}

/* ══════════════════════════════════════════════════════════════════════════════
   HELPERS: API
   ══════════════════════════════════════════════════════════════════════════════ */

/**
 * Faz uma chamada à API REST.
 * @param {string} path  — ex.: '/devices' ou '/devices/1/sensor'
 * @param {object} opts  — opções fetch opcionais
 * @returns {Promise<object>} resposta JSON { success, data, message }
 */
async function api(path, opts = {}) {
  const url = CONFIG.apiBase + path;
  const defaults = {
    headers: { 'Content-Type': 'application/json' },
  };
  try {
    const res = await fetch(url, { ...defaults, ...opts });
    const json = await res.json();
    if (!json.success && res.status >= 400) {
      throw new Error(json.message || `Erro ${res.status}`);
    }
    return json;
  } catch (err) {
    if (!navigator.onLine) {
      state.offline = true;
      updateOnlineIndicator(false);
    }
    throw err;
  }
}

const apiGet  = (path) => api(path);
const apiPost = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
const apiDel  = (path) => api(path, { method: 'DELETE' });

/* ══════════════════════════════════════════════════════════════════════════════
   HELPERS: UI
   ══════════════════════════════════════════════════════════════════════════════ */

/**
 * Exibe uma notificação toast temporária.
 * @param {string} msg     — texto da mensagem
 * @param {'success'|'error'|'info'} type
 * @param {number} duration — ms antes de desaparecer
 */
function toast(msg, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

/** Formata uma data ISO para exibição local */
function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return iso; }
}

/** Formata data relativa (ex.: "há 2 min") */
function fmtRelative(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1)  return 'agora mesmo';
  if (mins < 60) return `há ${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `há ${hrs} h`;
  return `há ${Math.floor(hrs / 24)} dias`;
}

function updateOnlineIndicator(online) {
  const dot = document.querySelector('.status-dot');
  const lbl = document.querySelector('.topbar-status span');
  if (dot) dot.classList.toggle('online', online);
  if (lbl) lbl.textContent = online ? 'Online' : 'Offline';
}

window.addEventListener('online',  () => { state.offline = false; updateOnlineIndicator(true);  toast('Conexão restaurada', 'success'); });
window.addEventListener('offline', () => { state.offline = true;  updateOnlineIndicator(false); toast('Sem conexão à internet', 'error'); });

/* ══════════════════════════════════════════════════════════════════════════════
   NAVEGAÇÃO
   ══════════════════════════════════════════════════════════════════════════════ */

function navigate(section) {
  // Desactivar todas as secções e itens de nav
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item, .sidebar-item').forEach(i => i.classList.remove('active'));

  // Activar secção alvo
  const el = document.getElementById(`section-${section}`);
  if (el) el.classList.add('active');

  // Activar item de nav correspondente
  document.querySelectorAll(`[data-nav="${section}"]`).forEach(i => i.classList.add('active'));

  // Limpar timers anteriores
  Object.values(state.timers).forEach(clearInterval);
  state.timers = {};

  // Carregar dados e configurar auto-refresh
  switch (section) {
    case 'dashboard':
      loadDashboard();
      state.timers.dash = setInterval(loadDashboard, CONFIG.dashboardRefresh);
      break;
    case 'sensors':
      loadDevicesIntoSelects();
      loadSensors();
      state.timers.sensors = setInterval(loadSensors, CONFIG.sensorRefresh);
      break;
    case 'sms':
      loadDevicesIntoSelects();
      loadSMS();
      break;
    case 'logs':
      loadDevicesIntoSelects();
      loadLogs();
      state.timers.logs = setInterval(loadLogs, CONFIG.logsRefresh);
      break;
  }
}

/* ══════════════════════════════════════════════════════════════════════════════
   SECÇÃO: DASHBOARD
   ══════════════════════════════════════════════════════════════════════════════ */

async function loadDashboard() {
  const container = document.getElementById('dashboard-cards');
  if (!container) return;

  try {
    const res = await apiGet('/devices');
    state.devices = res.data || [];

    if (state.devices.length === 0) {
      container.innerHTML = `
        <div class="card text-center" style="grid-column:1/-1; padding:2rem;">
          <p class="text-muted">Nenhum dispositivo registado.</p>
          <button class="btn btn-primary mt-2" onclick="openAddDeviceModal()">+ Adicionar Dispositivo</button>
        </div>`;
      return;
    }

    container.innerHTML = state.devices.map(renderDeviceCard).join('');
    updateOnlineIndicator(true);
  } catch (err) {
    container.innerHTML = `<div class="card text-center" style="grid-column:1/-1;padding:2rem;">
      <p class="text-muted">Erro ao carregar dispositivos: ${err.message}</p>
    </div>`;
  }
}

function renderDeviceCard(device) {
  const statusClass = device.status === 'online' ? 'online' : 'offline';
  const lastReading = ''; // será preenchido via fetch adicional se necessário

  return `
    <div class="device-card ${statusClass}" id="device-card-${device.id}">
      <div class="device-header">
        <div>
          <div class="device-name">${escHtml(device.name)}</div>
          <div class="device-type">${escHtml(device.type)}</div>
          ${device.ip_address ? `<div class="device-ip">📡 ${escHtml(device.ip_address)}</div>` : ''}
        </div>
        <span class="badge badge-${statusClass}">${device.status}</span>
      </div>

      <div id="sensor-info-${device.id}">
        <div class="sensor-row">
          <div class="sensor-chip skeleton" style="width:90px;height:34px;"></div>
          <div class="sensor-chip skeleton" style="width:90px;height:34px;"></div>
        </div>
      </div>

      <div class="sensor-ts" id="sensor-ts-${device.id}">
        Última actualização: ${fmtRelative(device.last_seen)}
      </div>

      <div class="control-row">
        <button class="btn-control" onclick="sendCommand(${device.id},'LED1','ON')"  title="Ligar LED1">💡 LED1 ON</button>
        <button class="btn-control" onclick="sendCommand(${device.id},'LED1','OFF')" title="Desligar LED1">💡 LED1 OFF</button>
        <button class="btn-control" onclick="sendCommand(${device.id},'LED2','ON')"  title="Ligar LED2">💡 LED2 ON</button>
        <button class="btn-control" onclick="sendCommand(${device.id},'LED2','OFF')" title="Desligar LED2">💡 LED2 OFF</button>
        <button class="btn-control" onclick="sendCommand(${device.id},'RELAY','ON')"  title="Activar Relé">⚡ RELAY ON</button>
        <button class="btn-control" onclick="sendCommand(${device.id},'RELAY','OFF')" title="Desactivar Relé">⚡ RELAY OFF</button>
      </div>
    </div>`;
}

// Carregar última leitura de sensor para cada device card
async function loadLatestSensors() {
  for (const device of state.devices) {
    try {
      const res = await apiGet(`/devices/${device.id}/sensor/history?limit=1`);
      const readings = res.data || [];
      const sensorEl = document.getElementById(`sensor-info-${device.id}`);
      if (!sensorEl) continue;

      if (readings.length === 0) {
        sensorEl.innerHTML = `<p class="text-muted mb-1" style="font-size:0.75rem;">Sem leituras ainda</p>`;
        continue;
      }

      const r = readings[0];
      sensorEl.innerHTML = `
        <div class="sensor-row">
          <div class="sensor-chip">
            <span class="icon">🌡️</span>
            <span class="val">${r.temperature.toFixed(1)}</span>
            <span class="unit">°C</span>
          </div>
          <div class="sensor-chip">
            <span class="icon">💧</span>
            <span class="val">${r.humidity.toFixed(1)}</span>
            <span class="unit">%</span>
          </div>
        </div>`;

      const tsEl = document.getElementById(`sensor-ts-${device.id}`);
      if (tsEl) tsEl.textContent = `Última leitura: ${fmtRelative(r.recorded_at)}`;
    } catch { /* ignorar erros individuais */ }
  }
}

async function sendCommand(deviceId, target, state_) {
  try {
    await apiPost(`/devices/${deviceId}/control`, { target, state: state_ });
    toast(`Comando ${state_} → ${target} enviado`, 'success');
  } catch (err) {
    toast(`Erro: ${err.message}`, 'error');
  }
}

// Modal de adicionar dispositivo
function openAddDeviceModal() {
  const name  = prompt('Nome do dispositivo:');
  if (!name) return;
  const type  = prompt('Tipo (ex.: ESP32-S3, Arduino):');
  if (!type) return;

  apiPost('/devices', { name: name.trim(), type: type.trim() })
    .then(() => { toast('Dispositivo adicionado!', 'success'); loadDashboard(); })
    .catch(err => toast(`Erro: ${err.message}`, 'error'));
}

/* ══════════════════════════════════════════════════════════════════════════════
   SECÇÃO: SENSORES
   ══════════════════════════════════════════════════════════════════════════════ */

async function loadSensors() {
  const deviceId = document.getElementById('sensor-device-select')?.value;
  if (!deviceId) return;
  state.currentDeviceId = deviceId;

  try {
    const res = await apiGet(`/devices/${deviceId}/sensor/history?limit=100`);
    const readings = res.data || [];

    renderSensorChart(readings);
    renderSensorTable(readings);
    updateOnlineIndicator(true);
  } catch (err) {
    toast(`Erro ao carregar sensores: ${err.message}`, 'error');
  }
}

function renderSensorChart(readings) {
  const ctx = document.getElementById('sensor-chart');
  if (!ctx) return;

  const labels = readings.map(r => {
    const d = new Date(r.recorded_at);
    return `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`;
  });
  const temps = readings.map(r => r.temperature);
  const hums  = readings.map(r => r.humidity);

  if (state.sensorChart) {
    state.sensorChart.data.labels = labels;
    state.sensorChart.data.datasets[0].data = temps;
    state.sensorChart.data.datasets[1].data = hums;
    state.sensorChart.update('none');
    return;
  }

  // Chart.js disponível via CDN
  if (typeof Chart === 'undefined') {
    document.getElementById('chart-placeholder').textContent = 'Chart.js não carregado';
    return;
  }

  Chart.defaults.color = '#8b93b0';
  Chart.defaults.borderColor = '#2d3148';

  state.sensorChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Temperatura (°C)',
          data: temps,
          borderColor: '#f87171',
          backgroundColor: 'rgba(248,113,113,0.08)',
          tension: 0.4,
          fill: true,
          pointRadius: 3,
        },
        {
          label: 'Humidade (%)',
          data: hums,
          borderColor: '#60a5fa',
          backgroundColor: 'rgba(96,165,250,0.08)',
          tension: 0.4,
          fill: true,
          pointRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1e2130',
          borderColor: '#2d3148',
          borderWidth: 1,
          titleColor: '#e8eaf0',
          bodyColor: '#8b93b0',
        },
      },
      scales: {
        x: { grid: { color: '#2d3148' }, ticks: { maxTicksLimit: 10 } },
        y: { grid: { color: '#2d3148' }, beginAtZero: false },
      },
    },
  });
}

function renderSensorTable(readings) {
  const tbody = document.getElementById('sensor-table-body');
  if (!tbody) return;

  if (readings.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3" class="text-center text-muted" style="padding:1.5rem;">Sem leituras para este dispositivo</td></tr>`;
    return;
  }

  // Mostrar as 50 mais recentes na tabela
  tbody.innerHTML = readings.slice(-50).reverse().map(r => `
    <tr>
      <td class="td-mono">${fmtDate(r.recorded_at)}</td>
      <td><span style="color:var(--red)">${r.temperature.toFixed(2)} °C</span></td>
      <td><span style="color:var(--blue)">${r.humidity.toFixed(2)} %</span></td>
    </tr>`).join('');
}

/* ══════════════════════════════════════════════════════════════════════════════
   SECÇÃO: SMS
   ══════════════════════════════════════════════════════════════════════════════ */

async function loadSMS() {
  const deviceId = document.getElementById('sms-device-select')?.value;
  if (!deviceId) return;

  try {
    const res = await apiGet(`/sms?device_id=${deviceId}&limit=100`);
    const messages = res.data || [];
    state.smsCount = messages.length;

    renderChatWindow(messages);
    renderSMSCounter(messages);
  } catch (err) {
    toast(`Erro ao carregar SMS: ${err.message}`, 'error');
  }
}

function renderChatWindow(messages) {
  const win = document.getElementById('chat-window');
  if (!win) return;

  if (messages.length === 0) {
    win.innerHTML = `<div class="chat-empty">Nenhuma mensagem SMS encontrada</div>`;
    return;
  }

  win.innerHTML = messages.slice().reverse().map(m => `
    <div class="chat-msg ${m.direction.toLowerCase()}">
      <div>${escHtml(m.message_body)}</div>
      <div class="chat-msg-meta">
        ${m.direction === 'IN' ? '📨' : '📤'} ${escHtml(m.phone_number)}
        · ${fmtDate(m.received_at || m.created_at)}
        ${m.direction === 'OUT' ? (m.sent ? ' · <span style="color:var(--green)">✓ enviado</span>' : ' · <span style="color:var(--yellow)">pendente</span>') : ''}
      </div>
    </div>`).join('');

  // Scroll para o fundo
  win.scrollTop = win.scrollHeight;
}

function renderSMSCounter(messages) {
  const inMessages = messages.filter(m => m.direction === 'IN').length;
  const pct = Math.min((inMessages / CONFIG.sim900Max) * 100, 100);
  const fill = document.getElementById('sms-fill');
  const label = document.getElementById('sms-count-label');

  if (fill) {
    fill.style.width = `${pct}%`;
    fill.className = 'sms-counter-fill' + (pct >= 100 ? ' full' : pct >= 75 ? ' warn' : '');
  }
  if (label) {
    label.textContent = `${inMessages} / ${CONFIG.sim900Max} no SIM900`;
  }
}

async function sendSMS() {
  const deviceId = document.getElementById('sms-device-select')?.value;
  const phone    = document.getElementById('sms-phone')?.value.trim();
  const body     = document.getElementById('sms-body')?.value.trim();

  if (!deviceId) { toast('Seleccione um dispositivo', 'error'); return; }
  if (!phone)    { toast('Número de telefone obrigatório', 'error'); return; }
  if (!body)     { toast('Mensagem não pode estar vazia', 'error'); return; }
  if (body.length > 160) { toast('Máximo 160 caracteres', 'error'); return; }

  try {
    await apiPost('/sms/send', { device_id: parseInt(deviceId), phone, body });
    toast('SMS enfileirado para envio!', 'success');
    document.getElementById('sms-phone').value = '';
    document.getElementById('sms-body').value  = '';
    loadSMS();
  } catch (err) {
    toast(`Erro: ${err.message}`, 'error');
  }
}

/* ══════════════════════════════════════════════════════════════════════════════
   SECÇÃO: LOGS / HISTÓRICO
   ══════════════════════════════════════════════════════════════════════════════ */

async function loadLogs() {
  const deviceId  = document.getElementById('logs-device-select')?.value;
  const statusFlt = document.getElementById('logs-status-filter')?.value || 'all';
  if (!deviceId) return;

  try {
    const res = await apiGet(`/devices/${deviceId}/commands?status=${statusFlt}&limit=200`);
    renderLogsTable(res.data || []);
  } catch (err) {
    toast(`Erro ao carregar logs: ${err.message}`, 'error');
  }
}

function renderLogsTable(commands) {
  const tbody = document.getElementById('logs-table-body');
  if (!tbody) return;

  if (commands.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="text-center text-muted" style="padding:1.5rem;">Nenhum comando encontrado</td></tr>`;
    return;
  }

  tbody.innerHTML = commands.map(c => `
    <tr>
      <td class="td-mono">${fmtDate(c.created_at)}</td>
      <td><span class="chip chip-${c.target.toLowerCase()}" style="background:var(--accent-glow);color:var(--accent)">${escHtml(c.target)}</span></td>
      <td><span class="chip chip-${c.command.toLowerCase()}">${escHtml(c.command)}</span></td>
      <td><span class="chip ${c.executed ? 'chip-executed' : 'chip-pending'}">${c.executed ? 'executado' : 'pendente'}</span></td>
    </tr>`).join('');
}

/* ══════════════════════════════════════════════════════════════════════════════
   SELECTORES DE DISPOSITIVO (reutilizados nas secções)
   ══════════════════════════════════════════════════════════════════════════════ */

async function loadDevicesIntoSelects() {
  try {
    if (state.devices.length === 0) {
      const res = await apiGet('/devices');
      state.devices = res.data || [];
    }

    const opts = state.devices.map(d =>
      `<option value="${d.id}">${escHtml(d.name)} (${escHtml(d.type)})</option>`
    ).join('');

    const emptyOpt = `<option value="">— Seleccionar dispositivo —</option>`;

    ['sensor-device-select', 'sms-device-select', 'logs-device-select'].forEach(id => {
      const sel = document.getElementById(id);
      if (sel) {
        const prev = sel.value;
        sel.innerHTML = emptyOpt + opts;
        if (prev) sel.value = prev;
      }
    });
  } catch { /* ignorar */ }
}

/* ══════════════════════════════════════════════════════════════════════════════
   SEGURANÇA: Escapar HTML para evitar XSS
   ══════════════════════════════════════════════════════════════════════════════ */

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/* ══════════════════════════════════════════════════════════════════════════════
   INICIALIZAÇÃO
   ══════════════════════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
  // Verificar conectividade inicial
  updateOnlineIndicator(navigator.onLine);

  // Navegar para dashboard por padrão
  navigate('dashboard');
  // Após carregar devices, preencher skeletons com dados reais
  setTimeout(loadLatestSensors, 800);
});
