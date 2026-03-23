const state = {
  bootstrap: null,
  me: null,
  summary: null,
  currentGroupId: null,
  requestedGroupId: null,
  currentModuleKey: 'verify',
  modulePayload: null,
  notice: '',
  noticeType: '',
  previewHtml: '',
};

const root = document.getElementById('app');
let previewTimer = null;
let previewRequestId = 0;
let scheduleItemSeq = 0;
let moderationRuleSeq = 0;

function isLoopbackHost() {
  return ['127.0.0.1', 'localhost', '::1'].includes(window.location.hostname);
}

function readRequestedGroupId() {
  const raw = new URLSearchParams(window.location.search).get('group_id');
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function syncGroupLocation() {
  const url = new URL(window.location.href);
  if (state.currentGroupId == null) {
    url.searchParams.delete('group_id');
  } else {
    url.searchParams.set('group_id', String(state.currentGroupId));
  }
  window.history.replaceState({}, '', url);
}

state.requestedGroupId = readRequestedGroupId();
function cloneData(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    credentials: 'same-origin',
    ...options,
  });
  if (response.status === 204) return null;
  const text = await response.text();
  const contentType = response.headers.get('content-type') || '';
  let data = null;
  if (text) {
    if (contentType.includes('application/json')) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    } else {
      data = text;
    }
  }
  if (!response.ok) {
    const message = (data && typeof data === 'object' && (data.detail || data.error)) || (typeof data === 'string' && data.trim()) || `HTTP_${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

function moduleMeta(key) {
  return (state.bootstrap?.modules || []).find((item) => item.key === key) || null;
}

function summaryModule(key) {
  return (state.summary?.modules || []).find((item) => item.key === key) || {};
}

async function loadBootstrap() {
  state.bootstrap = await api('/api/web/bootstrap');
}

async function loadSession() {
  try {
    state.me = await api('/api/web/me');
    const groups = state.me.groups || [];
    const requestedGroupId = state.requestedGroupId;
    const hasCurrentGroup = groups.some((group) => Number(group.id) === Number(state.currentGroupId));
    const hasRequestedGroup = requestedGroupId != null && groups.some((group) => Number(group.id) === Number(requestedGroupId));
    if (hasRequestedGroup) {
      state.currentGroupId = requestedGroupId;
    } else if (!hasCurrentGroup) {
      state.currentGroupId = groups[0]?.id || null;
    }
    if (requestedGroupId != null && !hasRequestedGroup) {
      state.notice = '当前群组不可管理，已切换到默认群组';
      state.noticeType = 'error';
    }
    if (state.currentGroupId) {
      syncGroupLocation();
      await loadGroup(state.currentGroupId, state.currentModuleKey);
    }
  } catch (error) {
    if (error.status === 401) {
      state.me = null;
      state.summary = null;
      state.modulePayload = null;
      return;
    }
    throw error;
  }
}

async function loadGroup(groupId, moduleKey = state.currentModuleKey) {
  state.currentGroupId = Number(groupId);
  syncGroupLocation();
  state.summary = await api(`/api/web/groups/${state.currentGroupId}/summary`);
  await loadModule(moduleKey || state.currentModuleKey || 'verify');
}

async function loadModule(moduleKey) {
  state.currentModuleKey = moduleKey;
  if (!state.currentGroupId) return;
  state.modulePayload = await api(`/api/web/groups/${state.currentGroupId}/module/${moduleKey}`);
  state.previewHtml = '';
  render();
}

function renderNotice() {
  if (!state.notice) return '';
  return `<div class="notice ${escapeHtml(state.noticeType)}">${escapeHtml(state.notice)}</div>`;
}

function noticeHost() {
  return root.querySelector('[data-notice-host]');
}

function previewHost() {
  return root.querySelector('[data-preview-host]');
}

function updateNotice() {
  const host = noticeHost();
  if (host) host.innerHTML = renderNotice();
}

function updatePreview() {
  queuePreviewRender();
}

function showNotice(text, type = 'ok') {
  state.notice = text;
  state.noticeType = type;
  if (noticeHost()) {
    updateNotice();
    return;
  }
  render();
}

function clearNotice() {
  state.notice = '';
  state.noticeType = '';
  updateNotice();
}

function renderLoginPanel() {
  const localDebug = state.bootstrap?.local_debug_login || {};
  if (isLoopbackHost()) {
    if (!localDebug.enabled) {
      return `
        <div class="login-widget-box local-debug-box">
          <div class="subtle">本机调试登录未开启。请在当前机器上设置 <code>WEB_LOCAL_DEBUG_LOGIN_ENABLED=1</code> 和 <code>WEB_LOCAL_DEBUG_LOGIN_SECRET</code> 后再使用。</div>
        </div>`;
    }
    return `
      <div class="login-widget-box local-debug-box">
        <div class="subtle">Telegram 登录组件无法在 localhost 或 127.0.0.1 上使用。本机调试登录仅限当前机器，并且需要输入已配置的调试口令。</div>
        <input type="password" id="local-debug-secret" placeholder="请输入本机调试口令" />
        <button class="primary-btn" data-action="local-debug-login">使用本机调试登录</button>
      </div>`;
  }
  return `<div id="telegram-login-box" class="login-widget-box">正在加载 Telegram 登录组件...</div>`;
}

function renderLogin() {

  root.className = 'app-shell';
  root.innerHTML = `
    <div class="login-shell">
      <div class="login-card">
        <span class="hero-tag">浅蓝风格管理后台</span>
        <h1 class="hero-title">群组管理后台</h1>
        <p class="hero-copy">你可以在公网域名下使用 Telegram 登录，也可以在当前机器上使用本机调试登录。网页后台与 Telegram 端共用同一套存储配置。</p>
        <div class="login-grid">
          <div class="soft-panel">
            <h3 class="section-title">当前能力</h3>
            <ul class="bullet-list">
              <li>Telegram 登录与会话管理</li>
              <li>24 个模块导航</li>
              <li>浅蓝风格后台界面</li>
              <li>消息预览面板</li>
              <li>统一消息编辑器</li>
              <li>保存后立即生效</li>
            </ul>
          </div>
          <div class="soft-panel">
            <h3 class="section-title">登录</h3>
            ${renderLoginPanel()}
          </div>
        </div>
      </div>
    </div>`;
  mountTelegramWidget();
}

function mountTelegramWidget() {
  const target = document.getElementById('telegram-login-box');
  if (!target || !state.bootstrap?.bot_username || isLoopbackHost()) return;
  target.innerHTML = '';
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://telegram.org/js/telegram-widget.js?22';
  script.setAttribute('data-telegram-login', state.bootstrap.bot_username);
  script.setAttribute('data-size', 'large');
  script.setAttribute('data-userpic', 'false');
  script.setAttribute('data-request-access', 'write');
  script.setAttribute('data-onauth', 'window.onTelegramAuth(user)');
  target.appendChild(script);
}

async function localDebugLogin() {
  try {
    const secret = document.getElementById('local-debug-secret')?.value || '';
    await api('/api/web/auth/local-debug', { method: 'POST', body: JSON.stringify({ secret }) });
    clearNotice();
    await loadSession();
    render();
  } catch (error) {
    showNotice(humanizeErrorMessage(error.message, '本机调试登录失败，请检查本地服务日志'), 'error');
  }
}

window.onTelegramAuth = async (user) => {
  try {
    await api('/api/web/auth/telegram', { method: 'POST', body: JSON.stringify(user) });
    clearNotice();
    await loadSession();
    render();
  } catch (error) {
    showNotice(humanizeErrorMessage(error.message, 'Telegram 登录失败，请重试'), 'error');
  }
};

function groups() {
  return state.me?.groups || [];
}

function runtimePreviewLines(item) {
  return (Array.isArray(item?.runtime_preview) ? item.runtime_preview : []).filter((value) => value != null && String(value).trim() !== '').slice(0, 2);
}

function runtimeAlertLines(item) {
  return (Array.isArray(item?.runtime_alerts) ? item.runtime_alerts : []).filter((value) => value != null && String(value).trim() !== '').slice(0, 2);
}

function runtimeAlertDetails(item) {
  return (Array.isArray(item?.runtime_alert_details) ? item.runtime_alert_details : []).filter((entry) => entry && String(entry.message || '').trim());
}

function renderSidebarModules() {
  return (state.summary?.modules || []).map((item) => {
    const runtimeLines = runtimePreviewLines(item);
    const alertLines = runtimeAlertLines(item);
    return `
    <button class="module-tile ${item.key === state.currentModuleKey ? 'active' : ''} ${alertLines.length ? 'has-alert' : ''}" data-action="select-module" data-module="${item.key}">
      <div class="tile-head">
        <span>${escapeHtml(item.icon)}</span>
        <span>${escapeHtml(item.label)}</span>
        ${alertLines.length ? `<span class="tile-alert-count">${alertLines.length}</span>` : ''}
      </div>
      <div class="tile-summary">${escapeHtml(item.summary || '-')}</div>
      ${runtimeLines.length ? `<div class="tile-runtime">${runtimeLines.map((line) => `<div class="tile-runtime-line">${escapeHtml(line)}</div>`).join('')}</div>` : ''}
      ${alertLines.length ? `<div class="tile-alert">${alertLines.map((line) => `<div class="tile-alert-line">${escapeHtml(line)}</div>`).join('')}</div>` : ''}
    </button>`;
  }).join('');
}

function renderGroupOverviewCard() {
  const keys = ['verify', 'schedule', 'admin_access', 'nsfw', 'invite', 'fun'];
  const items = keys.map((key) => summaryModule(key)).filter((item) => item && item.key);
  if (!items.length) return '';
  return `
    <section class="section-card group-overview-card">
      <div>
        <p class="eyebrow">群组概览</p>
        <h3 class="section-title">${escapeHtml(state.summary?.group_title || '群组')}</h3>
      </div>
      <div class="overview-list">
        ${items.map((item) => {
          const preview = runtimePreviewLines(item);
          const alerts = runtimeAlertLines(item);
          const detail = [item.summary || '', ...preview].filter((value) => value && String(value).trim()).slice(0, 2).join(' / ');
          return `
            <div class="overview-row">
              <div class="overview-label-row">
                <div class="overview-label">${escapeHtml(item.label || item.key)}</div>
                ${alerts.length ? `<span class="overview-alert-count">${alerts.length} 条提醒</span>` : ''}
              </div>
              <div class="subtle">${escapeHtml(detail || '-')}</div>
              ${alerts.length ? `<div class="overview-alert">${alerts.map((line) => escapeHtml(line)).join(' / ')}</div>` : ''}
            </div>`;
        }).join('')}
      </div>
    </section>`;
}

function renderShell() {
  root.className = 'app-shell';
  const currentMeta = moduleMeta(state.currentModuleKey) || {};
  const currentSummary = summaryModule(state.currentModuleKey) || {};
  const currentPreview = runtimePreviewLines(currentSummary);
  const currentAlerts = runtimeAlertLines(currentSummary);
  root.innerHTML = `
    <div class="layout">
      <aside class="panel sidebar">
        <div class="sidebar-header">
          <p class="eyebrow">登录信息</p>
          <h2 class="panel-title">${escapeHtml(state.me?.user?.first_name || '')}</h2>
          <div class="user-line">@${escapeHtml(state.me?.user?.username || '-')}</div>
        </div>
        <div class="sidebar-body">
          <div class="user-card">
            <div class="field">
              <label class="field-label">当前群组</label>
              <select id="group-select">
                ${groups().map((group) => `<option value="${group.id}" ${Number(group.id) === Number(state.currentGroupId) ? 'selected' : ''}>${escapeHtml(group.title)}</option>`).join('')}
              </select>
            </div>
            <button class="secondary-btn" data-action="logout">退出登录</button>
          </div>
          ${renderGroupOverviewCard()}
          <div>
            <p class="eyebrow">24 个模块</p>
            <div class="module-grid">${renderSidebarModules()}</div>
          </div>
        </div>
      </aside>
      <main class="panel content">
        <div class="content-header">
          <div class="module-header-row">
            <div class="module-summary-stack">
              <p class="eyebrow">当前模块</p>
              <h2 class="panel-title">${escapeHtml(currentMeta.label || '')}</h2>
              <div class="subtle">${escapeHtml(currentSummary.summary || '')}</div>
              ${currentPreview.length ? `<div class="module-runtime-inline">${currentPreview.map((line) => `<span class="runtime-chip">${escapeHtml(line)}</span>`).join('')}</div>` : ''}
              ${currentAlerts.length ? `<div class="module-alert-inline">${currentAlerts.map((line) => `<span class="alert-chip">${escapeHtml(line)}</span>`).join('')}</div>` : ''}
            </div>
            <div class="inline-actions">
              <button class="primary-btn" data-action="save-module">保存</button>
              <button class="secondary-btn" data-action="reload-module">刷新</button>
            </div>
          </div>
          <div data-notice-host>${renderNotice()}</div>
        </div>
        <div class="content-body">${renderModuleEditor()}</div>
      </main>
      <section class="panel preview">
        <div class="preview-header">
          <p class="eyebrow">消息预览</p>
          <h2 class="panel-title">预览</h2>
          <div class="subtle">表单内容变更后，预览会自动刷新。</div>
        </div>
        <div class="preview-body" data-preview-host>${renderPreview()}</div>
      </section>
    </div>`;
}

function editorHintText(editor) {
  if (editor === 'json') {
    return '当前模块仍使用高级 JSON 编辑器。';
  }
  if (['welcome', 'verify', 'autoreply', 'invite'].includes(editor)) {
    return '支持按钮和消息预览的富文本编辑器。';
  }
  return '适用于常用配置项的结构化表单编辑器。';
}

function runtimeValueText(key, value) {
  if (Array.isArray(value)) return value.length ? value.map((item) => runtimeValueText(key, item)).join('、') : '-';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (value == null || value === '') return '-';
  if (typeof value === 'string') return optionLabel(RUNTIME_VALUE_LABELS[key], value);
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
function runtimeLabel(key) {
  return RUNTIME_KEY_LABELS[key] || String(key || '').replaceAll('_', ' ');
}

function renderRuntimeCard(runtime, alertDetails = []) {
  const entries = Object.entries(runtime || {}).filter(([key]) => !['group_id', 'group_title'].includes(key));
  const groups = [
    ['error', '错误'],
    ['warning', '警告'],
    ['info', '提示'],
  ].map(([severity, label]) => ({
    severity,
    label,
    items: alertDetails.filter((entry) => String(entry?.severity || 'warning') === severity),
  })).filter((group) => group.items.length);
  if (!entries.length && !groups.length) return '';
  return `
    <section class="section-card">
      <h3 class="section-title">运行状态</h3>
      <div class="subtle">这里显示该模块当前的实时后端状态。</div>
      ${entries.length ? `<div class="field-grid">
        ${entries.map(([key, value]) => `
          <div class="field">
            <label class="field-label">${escapeHtml(runtimeLabel(key))}</label>
            <div class="subtle">${escapeHtml(runtimeValueText(key, value))}</div>
          </div>`).join('')}
      </div>` : ''}
      ${groups.length ? `<div class="runtime-alert-groups">
        ${groups.map((group) => `<div class="runtime-alert-group ${escapeHtml(group.severity)}"><div class="runtime-alert-title">${escapeHtml(group.label)}</div><div class="runtime-alert-list">${group.items.map((entry) => `<div class="runtime-alert-item ${escapeHtml(group.severity)}">${escapeHtml(entry.message || '')}</div>`).join('')}</div></div>`).join('')}
      </div>` : ''}
    </section>`;
}

function renderModuleEditor() {
  if (!state.modulePayload) {
    return `<div class="section-card"><div class="subtle">濮濓絽婀崝鐘烘祰濡€虫健...</div></div>`;
  }
  const meta = state.modulePayload.module || {};
  const editor = state.modulePayload.editor;
  const data = state.modulePayload.data || {};
  const summary = summaryModule(state.currentModuleKey) || {};
  const prefix = `
    <section class="section-card">
      <h3 class="section-title">${escapeHtml(meta.label || '')}</h3>
      <div class="subtle">${escapeHtml(editorHintText(editor))}</div>
    </section>` + renderRuntimeCard(state.modulePayload.runtime || null, runtimeAlertDetails(summary));
  if (editor === 'welcome') return prefix + renderWelcomeEditor(data);
  if (editor === 'verify') return prefix + renderVerifyEditor(data);
  if (editor === 'autoreply') return prefix + renderAutoReplyEditor(data);
  if (editor === 'crypto') return prefix + renderCryptoEditor(data);
  if (editor === 'fun') return prefix + renderFunEditor(data);
  if (editor === 'invite') return prefix + renderInviteEditor(data);
  if (editor === 'lottery') return prefix + renderLotteryEditor(data);
  if (editor === 'points') return prefix + renderPointsEditor(data);
  if (editor === 'activity') return prefix + renderActivityEditor(data);
  if (editor === 'usdt') return prefix + renderUsdtEditor(data);
  if (editor === 'verified') return prefix + renderVerifiedEditor(data);
  if (editor === 'autoban') return prefix + renderAutobanEditor(data);
  if (editor === 'automute') return prefix + renderAutomuteEditor(data);
  if (editor === 'autowarn') return prefix + renderAutowarnEditor(data);
  if (editor === 'ad') return prefix + renderAdEditor(data);
  if (editor === 'cmd') return prefix + renderCommandGateEditor(data);
  if (editor === 'member') return prefix + render群成员Editor(data);
  if (editor === 'antispam') return prefix + renderAntiSpamEditor(data);
  if (editor === 'related') return prefix + renderRelatedEditor(data);
  if (editor === 'admin_access') return prefix + renderAdminAccessEditor(data);
  if (editor === 'nsfw') return prefix + renderNsfwEditor(data);
  if (editor === 'lang') return prefix + renderLanguageEditor(data);
  if (editor === 'schedule') return prefix + renderScheduleEditor(data);
  if (editor === 'autodelete') return prefix + renderAutodeleteEditor(data);
  return prefix + renderJsonEditor(data);
}

function buttonItemHtml(button = {}) {
  return `
    <div class="button-item" data-button-item>
      <input type="text" data-role="button-text" placeholder="\u6309\u94ae\u6587\u5b57" value="${escapeHtml(button.text || '')}" />
      <select data-role="button-type">
        ${renderOptionList(['url', 'callback'], button.type, BUTTON_TYPE_LABELS)}
        
      </select>
      <input type="text" data-role="button-value" placeholder="按钮值" value="${escapeHtml(button.value || '')}" />
      <input type="number" data-role="button-row" value="${escapeHtml(button.row ?? 0)}" min="0" max="9" />
      <button type="button" class="danger-btn" data-action="remove-button">\u5220\u9664</button>
    </div>`;
}

function messageEditorHtml(prefix, model, title) {
  const buttons = Array.isArray(model.buttons) ? model.buttons : [];
  return `
    <div class="message-editor" data-message-editor="${prefix}">
      <div class="helper-row">
        <h4 class="section-title">${escapeHtml(title)}</h4>
        <button type="button" class="secondary-btn" data-action="add-button">\u6dfb\u52a0\u6309\u94ae</button>
      </div>
      <div class="field full">
        <label class="field-label">\u6d88\u606f\u6587\u672c</label>
        <textarea data-field="${prefix}-text">${escapeHtml(model.text || '')}</textarea>
      </div>
      <div class="field full">
        <label class="field-label">图片文件 ID</label>
        <input type="text" data-field="${prefix}-photo" value="${escapeHtml(model.photo_file_id || '')}" />
      </div>
      <div class="button-list" data-button-list="${prefix}">
        ${buttons.map((button) => buttonItemHtml(button)).join('')}
      </div>
    </div>`;
}

function renderWelcomeEditor(data) {
  const model = { text: data.text || '', photo_file_id: data.photo_file_id || '', buttons: data.buttons || [] };
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="welcome-enabled" ${data.enabled ? 'checked' : ''} />\u5f00\u542f\u6b22\u8fce\u6d88\u606f</label>
        <label class="toggle"><input type="checkbox" id="welcome-delete-prev" ${data.delete_prev ? 'checked' : ''} />\u5220\u9664\u4e0a\u4e00\u6761\u6b22\u8fce\u6d88\u606f</label>
        <div class="field">
          <label class="field-label">自动删除时长（秒）</label>
          <input type="number" id="welcome-ttl" min="0" value="${escapeHtml(data.ttl_sec ?? 0)}" />
        </div>
      </div>
      ${messageEditorHtml('welcome', model, '\u6d88\u606f\u5185\u5bb9')}
    </section>`;
}

function renderVerifyEditor(data) {
  const targetsText = JSON.stringify(data.targets || [], null, 2);
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="verify-enabled" ${data.enabled ? 'checked' : ''} />\u5f00\u542f\u5165\u7fa4\u9a8c\u8bc1</label>
        <label class="toggle"><input type="checkbox" id="verify-private" ${data.private_enabled ? 'checked' : ''} />\u79c1\u804a\u9a8c\u8bc1</label>
        <div class="field">
          <label class="field-label">\u5f53\u524d\u6a21\u5f0f</label>
          <select id="verify-mode">
            ${['join', 'calc', 'image_calc', 'captcha'].map((mode) => `<option value="${mode}" ${data.mode === mode ? 'selected' : ''}>${escapeHtml(optionLabel(VERIFY_MODE_LABELS, mode))}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label class="field-label">\u9a8c\u8bc1\u65f6\u957f</label>
          <input type="number" id="verify-timeout" min="10" value="${escapeHtml(data.timeout_sec ?? 60)}" />
        </div>
        <div class="field">
          <label class="field-label">\u6700\u5927\u91cd\u8bd5\u6b21\u6570</label>
          <input type="number" id="verify-max-attempts" min="0" value="${escapeHtml(data.max_attempts ?? 3)}" />
        </div>
        <div class="field">
          <label class="field-label">\u5931\u8d25\u5904\u7406</label>
          <select id="verify-fail-action">
            ${['none', 'mute', 'kick', 'ban'].map((mode) => `<option value="${mode}" ${data.fail_action === mode ? 'selected' : ''}>${escapeHtml(optionLabel(VERIFY_FAIL_ACTION_LABELS, mode))}</option>`).join('')}
          </select>
        </div>
        <div class="field full">
          <label class="field-label">\u9a8c\u8bc1\u5931\u8d25\u6587\u6848</label>
          <textarea id="verify-fail-text">${escapeHtml(data.fail_text || '')}</textarea>
        </div>
        <div class="field full">
          <label class="field-label">验证目标 JSON</label>
          <textarea id="verify-targets-json">${escapeHtml(targetsText)}</textarea>
        </div>
      </div>
    </section>
    ${['join', 'calc', 'image_calc', 'captcha'].map((mode) => messageEditorHtml(`verify-${mode}`, data.messages?.[mode] || {}, `${optionLabel(VERIFY_MODE_LABELS, mode)}提示消息`)).join('')}`;
}

function autoReplyRuleHtml(rule = {}) {
  return `
    <div class="rule-card" data-auto-rule>
      <div class="helper-row">
        <h4 class="section-title">\u81ea\u52a8\u56de\u590d\u89c4\u5219</h4>
        <div class="inline-actions">
          <button type="button" class="secondary-btn" data-action="add-button">\u6dfb\u52a0\u6309\u94ae</button>
          <button type="button" class="danger-btn" data-action="remove-rule">\u5220\u9664\u89c4\u5219</button>
        </div>
      </div>
      <div class="field-grid">
        <div class="field">
          <label class="field-label">关键词</label>
          <input type="text" data-role="rule-keyword" value="${escapeHtml(rule.keyword || '')}" />
        </div>
        <div class="field">
          <label class="field-label">匹配方式</label>
          <select data-role="rule-mode">
            ${['contains', 'exact', 'regex'].map((mode) => `<option value="${mode}" ${rule.mode === mode ? 'selected' : ''}>${escapeHtml(optionLabel(MATCH_MODE_LABELS, mode))}</option>`).join('')}
          </select>
        </div>
        <label class="toggle"><input type="checkbox" data-role="rule-enabled" ${rule.enabled !== false ? 'checked' : ''} />\u542f\u7528\u89c4\u5219</label>
      </div>
      <div class="field full">
        <label class="field-label">\u56de\u590d\u6587\u672c</label>
        <textarea data-role="rule-reply-text">${escapeHtml(rule.reply_text || '')}</textarea>
      </div>
      <div class="field full">
        <label class="field-label">图片文件 ID</label>
        <input type="text" data-role="rule-photo" value="${escapeHtml(rule.photo_file_id || '')}" />
      </div>
      <div class="button-list" data-button-list="rule">
        ${(rule.buttons || []).map((button) => buttonItemHtml(button)).join('')}
      </div>
    </div>`;
}

function renderAutoReplyEditor(data) {
  const rules = Array.isArray(data.rules) ? data.rules : [];
  return `
    <section class="section-card">
      <div class="helper-row">
        <h3 class="section-title">\u81ea\u52a8\u56de\u590d\u89c4\u5219</h3>
        <button type="button" class="primary-btn" data-action="add-rule">\u6dfb\u52a0\u89c4\u5219</button>
      </div>
      <div class="rule-list" id="auto-rule-list">
        ${rules.map((rule) => autoReplyRuleHtml(rule)).join('') || `<div class="subtle">\u6682\u65e0\u89c4\u5219</div>`}
      </div>
    </section>`;
}

function renderCryptoEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="crypto-wallet-query" ${data.wallet_query_enabled ? 'checked' : ''} />启用钱包查询</label>
        <label class="toggle"><input type="checkbox" id="crypto-price-query" ${data.price_query_enabled ? 'checked' : ''} />启用价格查询</label>
        <label class="toggle"><input type="checkbox" id="crypto-price-push" ${data.push_enabled ? 'checked' : ''} />启用价格推送</label>
        <div class="field">
          <label class="field-label">默认币种</label>
          <input type="text" id="crypto-default-symbol" value="${escapeHtml(data.default_symbol || 'BTC')}" />
        </div>
        <div class="field">
          <label class="field-label">查询别名</label>
          <input type="text" id="crypto-query-alias" value="${escapeHtml(data.query_alias || '')}" />
        </div>
      </div>
    </section>`;
}

function renderFunEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="fun-dice-enabled" ${data.dice_enabled ? 'checked' : ''} />启用骰子</label>
        <label class="toggle"><input type="checkbox" id="fun-gomoku-enabled" ${data.gomoku_enabled ? 'checked' : ''} />启用五子棋</label>
        <div class="field">
          <label class="field-label">骰子消耗</label>
          <input type="number" id="fun-dice-cost" min="0" value="${escapeHtml(data.dice_cost ?? 0)}" />
        </div>
        <div class="field">
          <label class="field-label">骰子命令</label>
          <input type="text" id="fun-dice-command" value="${escapeHtml(data.dice_command || '/dice')}" />
        </div>
        <div class="field">
          <label class="field-label">五子棋命令</label>
          <input type="text" id="fun-gomoku-command" value="${escapeHtml(data.gomoku_command || '/gomoku')}" />
        </div>
      </div>
    </section>`;
}

function renderLotteryEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="lottery-enabled" ${data.enabled ? 'checked' : ''} />启用抽奖命令</label>
        <label class="toggle"><input type="checkbox" id="lottery-pin-post" ${data.pin_post ? 'checked' : ''} />置顶抽奖消息</label>
        <label class="toggle"><input type="checkbox" id="lottery-pin-result" ${data.pin_result ? 'checked' : ''} />置顶抽奖结果</label>
        <div class="field">
          <label class="field-label">查询命令</label>
          <input type="text" id="lottery-query-command" value="${escapeHtml(data.query_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">自动删除秒数</label>
          <input type="number" id="lottery-auto-delete" min="0" value="${escapeHtml(data.auto_delete_sec ?? 0)}" />
        </div>
      </div>
    </section>`;
}

function renderInviteEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="invite-enabled" ${data.enabled ? 'checked' : ''} />启用邀请追踪</label>
        <label class="toggle"><input type="checkbox" id="invite-notify-enabled" ${data.notify_enabled ? 'checked' : ''} />发送入群通知</label>
        <label class="toggle"><input type="checkbox" id="invite-join-review" ${data.join_review ? 'checked' : ''} />审核入群请求</label>
        <label class="toggle"><input type="checkbox" id="invite-admin-rank-only" ${data.only_admin_can_query_rank ? 'checked' : ''} />仅管理员可查询排名</label>
        <div class="field">
          <label class="field-label">奖励积分</label>
          <input type="number" id="invite-reward-points" min="0" value="${escapeHtml(data.reward_points ?? 0)}" />
        </div>
        <div class="field">
          <label class="field-label">查询命令</label>
          <input type="text" id="invite-query-command" value="${escapeHtml(data.query_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">今日排名命令</label>
          <input type="text" id="invite-today-rank-command" value="${escapeHtml(data.today_rank_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">本月排名命令</label>
          <input type="text" id="invite-month-rank-command" value="${escapeHtml(data.month_rank_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">总排名命令</label>
          <input type="text" id="invite-total-rank-command" value="${escapeHtml(data.total_rank_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">结果格式</label>
          <input type="text" id="invite-result-format" value="${escapeHtml(data.result_format || 'text')}" />
        </div>
        <div class="field">
          <label class="field-label">自动删除秒数</label>
          <input type="number" id="invite-auto-delete" min="0" value="${escapeHtml(data.auto_delete_sec ?? 0)}" />
        </div>
      </div>
      ${messageEditorHtml('invite-notify', data.notify_message || {}, '邀请通知消息')}
    </section>`;
}

function renderPointsEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="points-enabled" ${data.enabled ? 'checked' : ''} />启用积分</label>
        <label class="toggle"><input type="checkbox" id="points-chat-enabled" ${data.chat_points_enabled ? 'checked' : ''} />启用聊天积分</label>
        <label class="toggle"><input type="checkbox" id="points-admin-adjust-enabled" ${data.admin_adjust_enabled ? 'checked' : ''} />允许管理员调整</label>
        <div class="field">
          <label class="field-label">签到命令</label>
          <input type="text" id="points-sign-command" value="${escapeHtml(data.sign_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">查询命令</label>
          <input type="text" id="points-query-command" value="${escapeHtml(data.query_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">排行命令</label>
          <input type="text" id="points-rank-command" value="${escapeHtml(data.rank_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">签到积分</label>
          <input type="number" id="points-sign-points" min="0" value="${escapeHtml(data.sign_points ?? 0)}" />
        </div>
        <div class="field">
          <label class="field-label">每条消息积分</label>
          <input type="number" id="points-chat-per-message" min="0" value="${escapeHtml(data.chat_points_per_message ?? 0)}" />
        </div>
        <div class="field">
          <label class="field-label">最小文本长度</label>
          <input type="number" id="points-min-text-length" min="0" value="${escapeHtml(data.min_text_length ?? 0)}" />
        </div>
      </div>
    </section>`;
}

function renderActivityEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="activity-enabled" ${data.enabled ? 'checked' : ''} />启用活跃度排行</label>
        <div class="field">
          <label class="field-label">今日命令</label>
          <input type="text" id="activity-today-command" value="${escapeHtml(data.today_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">本月命令</label>
          <input type="text" id="activity-month-command" value="${escapeHtml(data.month_command || '')}" />
        </div>
        <div class="field">
          <label class="field-label">总计命令</label>
          <input type="text" id="activity-total-command" value="${escapeHtml(data.total_command || '')}" />
        </div>
      </div>
    </section>`;
}

function renderUsdtEditor(data) {
  const exchanges = Array.isArray(data.exchanges) ? data.exchanges : [];
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="usdt-enabled" ${data.enabled ? 'checked' : ''} />启用 USDT 工具</label>
        <label class="toggle"><input type="checkbox" id="usdt-show-query" ${data.show_query_message ? 'checked' : ''} />启用价格查询别名</label>
        <label class="toggle"><input type="checkbox" id="usdt-show-calc" ${data.show_calc_message ? 'checked' : ''} />启用换算别名</label>
        <div class="field">
          <label class="field-label">档位</label>
          <input type="text" id="usdt-tier" value="${escapeHtml(data.tier || 'best')}" />
        </div>
        <div class="field">
          <label class="field-label">查询别名</label>
          <input type="text" id="usdt-alias-z" value="${escapeHtml(data.alias_z || '')}" />
        </div>
        <div class="field">
          <label class="field-label">人民币别名</label>
          <input type="text" id="usdt-alias-w" value="${escapeHtml(data.alias_w || '')}" />
        </div>
        <div class="field">
          <label class="field-label">USDT 别名</label>
          <input type="text" id="usdt-alias-k" value="${escapeHtml(data.alias_k || '')}" />
        </div>
        <div class="field full">
          <label class="field-label">交易所</label>
          <div class="inline-actions">
            <label class="toggle"><input type="checkbox" id="usdt-exchange-binance" ${exchanges.includes('binance') ? 'checked' : ''} />Binance</label>
            <label class="toggle"><input type="checkbox" id="usdt-exchange-okx" ${exchanges.includes('okx') ? 'checked' : ''} />OKX</label>
            <label class="toggle"><input type="checkbox" id="usdt-exchange-htx" ${exchanges.includes('htx') ? 'checked' : ''} />HTX</label>
          </div>
        </div>
      </div>
    </section>`;
}

function renderVerifiedEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="verified-enabled" ${data.enabled ? 'checked' : ''} />启用认证用户功能</label>
      </div>
    </section>`;
}

function moderationRuleConfig(kind) {
  return {
    autoban: { title: '自动封禁规则', containerId: 'autoban-rule-list', action: 'remove-autoban-rule', emptyText: '暂无自动封禁规则', durationLabel: '规则时长（秒）', withDuration: true },
    automute: { title: '自动禁言规则', containerId: 'automute-rule-list', action: 'remove-automute-rule', emptyText: '暂无自动禁言规则', durationLabel: '规则时长（秒）', withDuration: true },
    autowarn: { title: '自动警告规则', containerId: 'autowarn-rule-list', action: 'remove-autowarn-rule', emptyText: '暂无自动警告规则', withDuration: false },
  }[kind];
}

function moderationRuleHtml(kind, rule = {}) {
  const config = moderationRuleConfig(kind);
  return `
    <div class="rule-card" data-moderation-rule="${escapeHtml(kind)}">
      <input type="hidden" data-role="mod-rule-id" value="${escapeHtml(rule.id || '')}" />
      <div class="helper-row">
        <h4 class="section-title">${escapeHtml(config.title)}</h4>
        <button type="button" class="danger-btn" data-action="${escapeHtml(config.action)}">删除规则</button>
      </div>
      <div class="field-grid">
        <div class="field">
          <label class="field-label">关键词</label>
          <input type="text" data-role="mod-rule-keyword" value="${escapeHtml(rule.keyword || '')}" />
        </div>
        <div class="field">
          <label class="field-label">匹配方式</label>
          <select data-role="mod-rule-mode">
            ${['contains', 'exact', 'regex'].map((mode) => `<option value="${mode}" ${rule.mode === mode ? 'selected' : ''}>${escapeHtml(optionLabel(MATCH_MODE_LABELS, mode))}</option>`).join('')}
          </select>
        </div>
        ${config.withDuration ? `<div class="field">
          <label class="field-label">${escapeHtml(config.durationLabel)}</label>
          <input type="number" data-role="mod-rule-duration" min="0" value="${escapeHtml(rule.duration_sec ?? 0)}" />
        </div>` : ''}
      </div>
    </div>`;
}

function renderAutobanEditor(data) {
  const rules = Array.isArray(data.rules) ? data.rules : [];
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="autoban-enabled" ${data.enabled !== false ? 'checked' : ''} />启用自动封禁</label>
        <div class="field">
          <label class="field-label">默认封禁时长（秒）</label>
          <input type="number" id="autoban-default-duration" min="0" value="${escapeHtml(data.default_duration_sec ?? 86400)}" />
        </div>
      </div>
      <div class="subtle">规则时长优先使用规则自身的值；如果默认时长为 0，且规则没有覆盖值，则表示永久封禁。</div>
    </section>
    <section class="section-card">
      <div class="helper-row">
        <h3 class="section-title">自动封禁规则</h3>
        <button type="button" class="primary-btn" data-action="add-autoban-rule">添加规则</button>
      </div>
      <div class="rule-list" id="autoban-rule-list">
        ${rules.map((rule) => moderationRuleHtml('autoban', rule)).join('') || `<div class="subtle">暂无自动封禁规则</div>`}
      </div>
    </section>`;
}

function renderAutomuteEditor(data) {
  const rules = Array.isArray(data.rules) ? data.rules : [];
  return `
    <section class="section-card">
      <div class="field-grid">
        <div class="field">
          <label class="field-label">默认禁言时长（秒）</label>
          <input type="number" id="automute-default-duration" min="1" value="${escapeHtml(data.default_duration_sec ?? 60)}" />
        </div>
      </div>
      <div class="subtle">每条命中的规则都可以覆盖默认禁言时长。</div>
    </section>
    <section class="section-card">
      <div class="helper-row">
        <h3 class="section-title">自动禁言规则</h3>
        <button type="button" class="primary-btn" data-action="add-automute-rule">添加规则</button>
      </div>
      <div class="rule-list" id="automute-rule-list">
        ${rules.map((rule) => moderationRuleHtml('automute', rule)).join('') || `<div class="subtle">暂无自动禁言规则</div>`}
      </div>
    </section>`;
}

function renderAutowarnEditor(data) {
  const rules = Array.isArray(data.rules) ? data.rules : [];
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="autowarn-enabled" ${data.enabled !== false ? 'checked' : ''} />启用自动警告</label>
        <label class="toggle"><input type="checkbox" id="autowarn-cmd-mute-enabled" ${data.cmd_mute_enabled ? 'checked' : ''} />命令触发警告</label>
        <div class="field">
          <label class="field-label">警告上限</label>
          <input type="number" id="autowarn-limit" min="1" value="${escapeHtml(data.warn_limit ?? 3)}" />
        </div>
        <div class="field">
          <label class="field-label">处罚时长（秒）</label>
          <input type="number" id="autowarn-mute-seconds" min="1" value="${escapeHtml(data.mute_seconds ?? 86400)}" />
        </div>
        <div class="field">
          <label class="field-label">处罚动作</label>
          <select id="autowarn-action">
            ${['mute', 'kick'].map((action) => `<option value="${action}" ${data.action === action ? 'selected' : ''}>${escapeHtml(optionLabel(MODERATION_ACTION_LABELS, action))}</option>`).join('')}
          </select>
        </div>
        <div class="field full">
          <label class="field-label">警告文案</label>
          <textarea id="autowarn-text">${escapeHtml(data.warn_text || '')}</textarea>
        </div>
      </div>
      <div class="subtle">预览会反映警告次数增加时发送的提示文案。</div>
    </section>
    <section class="section-card">
      <div class="helper-row">
        <h3 class="section-title">自动警告规则</h3>
        <button type="button" class="primary-btn" data-action="add-autowarn-rule">添加规则</button>
      </div>
      <div class="rule-list" id="autowarn-rule-list">
        ${rules.map((rule) => moderationRuleHtml('autowarn', rule)).join('') || `<div class="subtle">暂无自动警告规则</div>`}
      </div>
    </section>`;
}

function renderRelatedEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="related-cancel-top-pin" ${data.cancel_top_pin ? 'checked' : ''} />取消自动置顶转发消息</label>
        <label class="toggle"><input type="checkbox" id="related-occupy-comment" ${data.occupy_comment ? 'checked' : ''} />抢占关联频道评论</label>
      </div>
      ${messageEditorHtml('related-comment', data.comment_message || {}, '关联频道评论消息')}
    </section>`;
}

function renderAdminAccessEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <div class="field">
          <label class="field-label">管理权限模式</label>
          <select id="admin-access-mode">
            <option value="all_admins" ${data.mode === 'all_admins' ? 'selected' : ''}>所有管理员</option>
            <option value="service_owner" ${data.mode === 'service_owner' ? 'selected' : ''}>仅服务拥有者</option>
          </select>
        </div>
      </div>
      <div class="subtle">运行时权限校验会同时作用于网页后台访问和 Telegram 管理流程。</div>
    </section>`;
}

function renderNsfwEditor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="nsfw-enabled" ${data.enabled ? 'checked' : ''} />启用涉黄过滤</label>
        <label class="toggle"><input type="checkbox" id="nsfw-allow-miss" ${data.allow_miss ? 'checked' : ''} />允许漏判</label>
        <label class="toggle"><input type="checkbox" id="nsfw-notice-enabled" ${data.notice_enabled ? 'checked' : ''} />发送删除提示</label>
        <div class="field">
          <label class="field-label">敏感度</label>
          <select id="nsfw-sensitivity">
            ${['low', 'medium', 'high'].map((level) => `<option value="${level}" ${data.sensitivity === level ? 'selected' : ''}>${escapeHtml(optionLabel(NSFW_SENSITIVITY_LABELS, level))}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label class="field-label">提示自动删除秒数</label>
          <input type="number" id="nsfw-delay-delete-sec" min="0" value="${escapeHtml(data.delay_delete_sec ?? 0)}" />
        </div>
      </div>
      <div class="subtle">敏感度和漏判设置会直接影响机器人运行时使用的实际判定阈值。</div>
    </section>`;
}

function renderLanguageEditor(data) {
  const allowedText = (Array.isArray(data.allowed) ? data.allowed : []).join('\n');
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="lang-enabled" ${data.enabled ? 'checked' : ''} />启用语言白名单</label>
        <div class="field full">
          <label class="field-label">允许的语言</label>
          <textarea id="lang-allowed" placeholder="en&#10;zh">${escapeHtml(allowedText)}</textarea>
        </div>
      </div>
      <div class="subtle">每行填写一个语言代码。系统会自动归一化，例如 <code>zh-CN</code> 会转换为 <code>zh</code>。</div>
    </section>`;
}

function renderAdEditor(data) {
  const toggles = [
    ['ad-nickname-enabled', '过滤可疑昵称', data.nickname_enabled],
    ['ad-sticker-enabled', '过滤广告贴纸', data.sticker_enabled],
    ['ad-message-enabled', '过滤广告消息', data.message_enabled],
    ['ad-block-channel-mask', '拦截发送者身份掩码', data.block_channel_mask],
  ];
  return `
    <section class="section-card">
      <div class="field-grid">
        ${toggles.map(([id, label, checked]) => `<label class="toggle"><input type="checkbox" id="${id}" ${checked ? 'checked' : ''} />${escapeHtml(label)}</label>`).join('')}
      </div>
    </section>`;
}

function renderCommandGateEditor(data) {
  const toggles = [
    ['cmd-sign', '屏蔽签到命令', data.sign],
    ['cmd-profile', '屏蔽资料命令', data.profile],
    ['cmd-warn', '屏蔽警告命令', data.warn],
    ['cmd-help', '屏蔽帮助命令', data.help],
    ['cmd-config', '屏蔽配置命令', data.config],
    ['cmd-ban', '屏蔽封禁命令', data.ban],
    ['cmd-kick', '屏蔽移出命令', data.kick],
    ['cmd-mute', '屏蔽禁言命令', data.mute],
  ];
  return `
    <section class="section-card">
      <div class="field-grid">
        ${toggles.map(([id, label, checked]) => `<label class="toggle"><input type="checkbox" id="${id}" ${checked ? 'checked' : ''} />${escapeHtml(label)}</label>`).join('')}
      </div>
      <div class="subtle">启用后的开关会阻止非管理员使用对应命令。</div>
    </section>`;
}

function render群成员Editor(data) {
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="member-nickname-detect" ${data.nickname_change_detect ? 'checked' : ''} />追踪昵称变更</label>
        <label class="toggle"><input type="checkbox" id="member-nickname-notice" ${data.nickname_change_notice ? 'checked' : ''} />发送昵称变更提醒</label>
      </div>
    </section>`;
}

function renderAntiSpamEditor(data) {
  const types = Array.isArray(data.types) ? data.types : [];
  const typeOptions = [
    ['text', '文本'],
    ['photo', '图片'],
    ['video', '视频'],
    ['document', '文档'],
    ['voice', '语音'],
    ['sticker', '贴纸'],
    ['link', '链接'],
  ];
  return `
    <section class="section-card">
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" id="antispam-enabled" ${data.enabled ? 'checked' : ''} />启用防刷屏</label>
        <div class="field">
          <label class="field-label">处理动作</label>
          <select id="antispam-action">
            ${['mute', 'ban'].map((action) => `<option value="${action}" ${data.action === action ? 'selected' : ''}>${escapeHtml(action)}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label class="field-label">禁言时长（秒）</label>
          <input type="number" id="antispam-mute-seconds" min="0" value="${escapeHtml(data.mute_seconds ?? 300)}" />
        </div>
        <div class="field">
          <label class="field-label">检测窗口（秒）</label>
          <input type="number" id="antispam-window-sec" min="1" value="${escapeHtml(data.window_sec ?? 10)}" />
        </div>
        <div class="field">
          <label class="field-label">触发阈值</label>
          <input type="number" id="antispam-threshold" min="1" value="${escapeHtml(data.threshold ?? 3)}" />
        </div>
        <div class="field full">
          <label class="field-label">监控的消息类型</label>
          <div class="inline-actions">
            ${typeOptions.map(([value, label]) => `<label class="toggle"><input type="checkbox" id="antispam-type-${value}" ${types.includes(value) ? 'checked' : ''} />${escapeHtml(label)}</label>`).join('')}
          </div>
        </div>
      </div>
      <div class="subtle">在设定时间窗口内，非管理员重复发送相同内容时，会触发所选处理动作。</div>
    </section>`;
}

function scheduleItemHtml(item = {}, key = '') {
  const buttons = Array.isArray(item.buttons) ? item.buttons : [];
  const intervalMinutes = Math.max(1, Math.round(Number(item.interval_sec || 3600) / 60));
  return `
    <div class="rule-card" data-schedule-item data-item-key="${escapeHtml(key || '')}">
      <input type="hidden" data-role="schedule-id" value="${escapeHtml(item.id ?? 0)}" />
      <input type="hidden" data-role="schedule-next-at" value="${escapeHtml(item.next_at ?? 0)}" />
      <div class="helper-row">
        <h4 class="section-title">定时消息</h4>
        <div class="inline-actions">
          <button type="button" class="secondary-btn" data-action="add-button">添加按钮</button>
          <button type="button" class="danger-btn" data-action="remove-schedule-item">删除项目</button>
        </div>
      </div>
      <div class="field-grid">
        <label class="toggle"><input type="checkbox" data-role="schedule-enabled" ${item.enabled !== false ? 'checked' : ''} />启用</label>
        <div class="field">
          <label class="field-label">间隔分钟</label>
          <input type="number" data-role="schedule-interval-minutes" min="1" value="${escapeHtml(intervalMinutes)}" />
        </div>
        <div class="field">
          <label class="field-label">下次执行时间戳</label>
          <input type="number" data-role="schedule-next-at-display" value="${escapeHtml(item.next_at ?? 0)}" disabled />
        </div>
        <div class="field full">
          <label class="field-label">消息文本</label>
          <textarea data-role="schedule-text">${escapeHtml(item.text || '')}</textarea>
        </div>
        <div class="field full">
          <label class="field-label">图片文件 ID</label>
          <input type="text" data-role="schedule-photo" value="${escapeHtml(item.photo_file_id || '')}" />
        </div>
      </div>
      <div class="button-list">
        ${buttons.map((button) => buttonItemHtml(button)).join('')}
      </div>
    </div>`;
}

function renderScheduleEditor(data) {
  const items = Array.isArray(data.items) ? data.items : [];
  return `
    <section class="section-card">
      <div class="helper-row">
        <label class="toggle"><input type="checkbox" id="schedule-enabled" ${data.enabled !== false ? 'checked' : ''} />启用定时模块</label>
        <button type="button" class="primary-btn" data-action="add-schedule-item">添加定时任务</button>
      </div>
      <div class="subtle">保存时会忽略空白的定时任务，已有的下次执行时间戳会被保留。</div>
      <div class="rule-list" id="schedule-item-list">
        ${items.map((item, index) => scheduleItemHtml(item, item.id || `existing-${index}`)).join('') || `<div class="subtle">暂无定时消息</div>`}
      </div>
    </section>`;
}

function deleteRuleHtml(rule = {}) {
  return `
    <div class="rule-card" data-delete-rule>
      <div class="helper-row">
        <h4 class="section-title">删除规则</h4>
        <button type="button" class="danger-btn" data-action="remove-delete-rule">删除规则</button>
      </div>
      <div class="field-grid">
        <div class="field">
          <label class="field-label">关键词</label>
          <input type="text" data-role="delete-rule-keyword" value="${escapeHtml(rule.keyword || '')}" />
        </div>
        <div class="field">
          <label class="field-label">匹配方式</label>
          <select data-role="delete-rule-mode">
            ${['contains', 'exact', 'regex'].map((mode) => `<option value="${mode}" ${rule.mode === mode ? 'selected' : ''}>${escapeHtml(optionLabel(MATCH_MODE_LABELS, mode))}</option>`).join('')}
          </select>
        </div>
      </div>
    </div>`;
}

function renderAutodeleteEditor(data) {
  const toggles = [
    ['autodelete-delete-system', '删除系统消息', data.delete_system],
    ['autodelete-delete-channel-mask', '删除发送者身份消息', data.delete_channel_mask],
    ['autodelete-delete-links', '删除链接消息', data.delete_links],
    ['autodelete-delete-long', '删除长消息', data.delete_long],
    ['autodelete-delete-videos', '删除视频', data.delete_videos],
    ['autodelete-delete-stickers', '删除贴纸', data.delete_stickers],
    ['autodelete-delete-forwarded', '删除转发消息', data.delete_forwarded],
    ['autodelete-delete-ad-stickers', '删除广告贴纸', data.delete_ad_stickers],
    ['autodelete-delete-archives', '删除压缩包文件', data.delete_archives],
    ['autodelete-delete-executables', '删除可执行文件', data.delete_executables],
    ['autodelete-delete-notice-text', '删除提示文案', data.delete_notice_text],
    ['autodelete-delete-documents', '删除文档', data.delete_documents],
    ['autodelete-delete-mentions', '删除提及消息', data.delete_mentions],
    ['autodelete-delete-other-commands', '删除斜杠命令', data.delete_other_commands],
    ['autodelete-delete-qr', '删除类似二维码的消息', data.delete_qr],
    ['autodelete-delete-edited', '删除编辑后的消息', data.delete_edited],
    ['autodelete-delete-member-emoji', '删除成员表情', data.delete_member_emoji],
    ['autodelete-delete-member-emoji-only', '删除纯表情消息', data.delete_member_emoji_only],
    ['autodelete-delete-external-reply', '删除外部回复', data.delete_external_reply],
    ['autodelete-delete-shared-contact', '删除共享联系人', data.delete_shared_contact],
    ['autodelete-exclude-admins', '排除管理员', data.exclude_admins],
  ];
  const stickerText = (Array.isArray(data.ad_sticker_ids) ? data.ad_sticker_ids : []).join('\n');
  const rules = Array.isArray(data.custom_rules) ? data.custom_rules : [];
  return `
    <section class="section-card">
      <div class="field-grid">
        ${toggles.map(([id, label, checked]) => `<label class="toggle"><input type="checkbox" id="${id}" ${checked ? 'checked' : ''} />${escapeHtml(label)}</label>`).join('')}
        <div class="field">
          <label class="field-label">长消息阈值</label>
          <input type="number" id="autodelete-long-length" min="1" value="${escapeHtml(data.long_length ?? 500)}" />
        </div>
        <div class="field full">
          <label class="field-label">广告贴纸 file_unique_id 列表</label>
          <textarea id="autodelete-sticker-ids">${escapeHtml(stickerText)}</textarea>
        </div>
      </div>
    </section>
    <section class="section-card">
      <div class="helper-row">
        <h3 class="section-title">自定义删除规则</h3>
        <button type="button" class="primary-btn" data-action="add-delete-rule">添加规则</button>
      </div>
      <div class="rule-list" id="delete-rule-list">
        ${rules.map((rule) => deleteRuleHtml(rule)).join('') || `<div class="subtle">暂无自定义规则</div>`}
      </div>
    </section>`;
}

function renderJsonEditor(data) {

  return `
    <section class="section-card json-editor">
      <div class="subtle">\u672a\u63a5\u5165\u4e13\u7528\u53ef\u89c6\u5316\u8868\u5355\u65f6\uff0c\u53ef\u76f4\u63a5\u7f16\u8f91 JSON \u3002</div>
      <textarea id="module-json" style="min-height: 420px;">${escapeHtml(JSON.stringify(data, null, 2))}</textarea>
    </section>`;
}

function collectButtonsFromList(listEl) {
  const items = [...(listEl?.querySelectorAll('[data-button-item]') || [])];
  return items.map((item) => ({
    text: item.querySelector('[data-role="button-text"]')?.value || '',
    type: item.querySelector('[data-role="button-type"]')?.value || 'url',
    value: item.querySelector('[data-role="button-value"]')?.value || '',
    row: Number(item.querySelector('[data-role="button-row"]')?.value || 0),
  }));
}

function collectMessage(prefix) {
  const editor = document.querySelector(`[data-message-editor="${prefix}"]`);
  return {
    text: editor?.querySelector(`[data-field="${prefix}-text"]`)?.value || '',
    photo_file_id: editor?.querySelector(`[data-field="${prefix}-photo"]`)?.value || '',
    buttons: collectButtonsFromList(editor?.querySelector(`[data-button-list="${prefix}"]`)),
  };
}

function collectPayload() {
  const key = state.currentModuleKey;
  if (key === 'welcome') {
    return {
      data: {
        enabled: document.getElementById('welcome-enabled')?.checked || false,
        delete_prev: document.getElementById('welcome-delete-prev')?.checked || false,
        ttl_sec: Number(document.getElementById('welcome-ttl')?.value || 0),
        ...collectMessage('welcome'),
      },
    };
  }
  if (key === 'verify') {
    let targets = [];
    try {
      targets = JSON.parse(document.getElementById('verify-targets-json')?.value || '[]');
    } catch {
      throw new Error('楠岃瘉鐩爣 JSON 鏍煎紡閿欒');
    }
    const messages = {};
    ['join', 'calc', 'image_calc', 'captcha'].forEach((mode) => {
      messages[mode] = collectMessage(`verify-${mode}`);
    });
    return {
      data: {
        enabled: document.getElementById('verify-enabled')?.checked || false,
        private_enabled: document.getElementById('verify-private')?.checked || false,
        mode: document.getElementById('verify-mode')?.value || 'join',
        timeout_sec: Number(document.getElementById('verify-timeout')?.value || 60),
        max_attempts: Number(document.getElementById('verify-max-attempts')?.value || 3),
        fail_action: document.getElementById('verify-fail-action')?.value || 'mute',
        fail_text: document.getElementById('verify-fail-text')?.value || '',
        targets,
        messages,
      },
    };
  }
  if (key === 'autoreply') {
    const rules = [...document.querySelectorAll('[data-auto-rule]')].map((ruleEl) => ({
      keyword: ruleEl.querySelector('[data-role="rule-keyword"]')?.value || '',
      mode: ruleEl.querySelector('[data-role="rule-mode"]')?.value || 'contains',
      enabled: ruleEl.querySelector('[data-role="rule-enabled"]')?.checked || false,
      reply_text: ruleEl.querySelector('[data-role="rule-reply-text"]')?.value || '',
      photo_file_id: ruleEl.querySelector('[data-role="rule-photo"]')?.value || '',
      buttons: collectButtonsFromList(ruleEl.querySelector('[data-button-list="rule"]')),
    }));
    return { data: { rules } };
  }
  if (key === 'crypto') {
    return {
      data: {
        wallet_query_enabled: document.getElementById('crypto-wallet-query')?.checked || false,
        price_query_enabled: document.getElementById('crypto-price-query')?.checked || false,
        push_enabled: document.getElementById('crypto-price-push')?.checked || false,
        default_symbol: document.getElementById('crypto-default-symbol')?.value || 'BTC',
        query_alias: document.getElementById('crypto-query-alias')?.value || '',
      },
    };
  }
  if (key === 'fun') {
    return {
      data: {
        dice_enabled: document.getElementById('fun-dice-enabled')?.checked || false,
        dice_cost: Number(document.getElementById('fun-dice-cost')?.value || 0),
        dice_command: document.getElementById('fun-dice-command')?.value || '/dice',
        gomoku_enabled: document.getElementById('fun-gomoku-enabled')?.checked || false,
        gomoku_command: document.getElementById('fun-gomoku-command')?.value || '/gomoku',
      },
    };
  }
  if (key === 'lottery') {
    return {
      data: {
        enabled: document.getElementById('lottery-enabled')?.checked || false,
        query_command: document.getElementById('lottery-query-command')?.value || '',
        auto_delete_sec: Number(document.getElementById('lottery-auto-delete')?.value || 0),
        pin_post: document.getElementById('lottery-pin-post')?.checked || false,
        pin_result: document.getElementById('lottery-pin-result')?.checked || false,
      },
    };
  }
  if (key === 'invite') {
    return {
      data: {
        enabled: document.getElementById('invite-enabled')?.checked || false,
        notify_enabled: document.getElementById('invite-notify-enabled')?.checked || false,
        join_review: document.getElementById('invite-join-review')?.checked || false,
        reward_points: Number(document.getElementById('invite-reward-points')?.value || 0),
        query_command: document.getElementById('invite-query-command')?.value || '',
        today_rank_command: document.getElementById('invite-today-rank-command')?.value || '',
        month_rank_command: document.getElementById('invite-month-rank-command')?.value || '',
        total_rank_command: document.getElementById('invite-total-rank-command')?.value || '',
        result_format: document.getElementById('invite-result-format')?.value || 'text',
        only_admin_can_query_rank: document.getElementById('invite-admin-rank-only')?.checked || false,
        auto_delete_sec: Number(document.getElementById('invite-auto-delete')?.value || 0),
        notify_message: collectMessage('invite-notify'),
      },
    };
  }
  if (key === 'autoban') {
    return {
      data: {
        enabled: document.getElementById('autoban-enabled')?.checked || false,
        default_duration_sec: Number(document.getElementById('autoban-default-duration')?.value || 0),
        rules: collectModerationRules('autoban'),
      },
    };
  }
  if (key === 'automute') {
    return {
      data: {
        default_duration_sec: Number(document.getElementById('automute-default-duration')?.value || 60),
        rules: collectModerationRules('automute'),
      },
    };
  }
  if (key === 'autowarn') {
    return {
      data: {
        enabled: document.getElementById('autowarn-enabled')?.checked || false,
        cmd_mute_enabled: document.getElementById('autowarn-cmd-mute-enabled')?.checked || false,
        warn_limit: Number(document.getElementById('autowarn-limit')?.value || 3),
        mute_seconds: Number(document.getElementById('autowarn-mute-seconds')?.value || 86400),
        action: document.getElementById('autowarn-action')?.value || 'mute',
        warn_text: document.getElementById('autowarn-text')?.value || '',
        rules: collectModerationRules('autowarn'),
      },
    };
  }
  if (key === 'ad') {
    return {
      data: {
        nickname_enabled: document.getElementById('ad-nickname-enabled')?.checked || false,
        sticker_enabled: document.getElementById('ad-sticker-enabled')?.checked || false,
        message_enabled: document.getElementById('ad-message-enabled')?.checked || false,
        block_channel_mask: document.getElementById('ad-block-channel-mask')?.checked || false,
      },
    };
  }
  if (key === 'cmd') {
    return {
      data: {
        sign: document.getElementById('cmd-sign')?.checked || false,
        profile: document.getElementById('cmd-profile')?.checked || false,
        warn: document.getElementById('cmd-warn')?.checked || false,
        help: document.getElementById('cmd-help')?.checked || false,
        config: document.getElementById('cmd-config')?.checked || false,
        ban: document.getElementById('cmd-ban')?.checked || false,
        kick: document.getElementById('cmd-kick')?.checked || false,
        mute: document.getElementById('cmd-mute')?.checked || false,
      },
    };
  }
  if (key === 'member') {
    return {
      data: {
        nickname_change_detect: document.getElementById('member-nickname-detect')?.checked || false,
        nickname_change_notice: document.getElementById('member-nickname-notice')?.checked || false,
      },
    };
  }
  if (key === 'antispam') {
    return {
      data: {
        enabled: document.getElementById('antispam-enabled')?.checked || false,
        action: document.getElementById('antispam-action')?.value || 'mute',
        mute_seconds: Number(document.getElementById('antispam-mute-seconds')?.value || 300),
        window_sec: Number(document.getElementById('antispam-window-sec')?.value || 10),
        threshold: Number(document.getElementById('antispam-threshold')?.value || 3),
        types: ['text', 'photo', 'video', 'document', 'voice', 'sticker', 'link'].filter((value) => document.getElementById(`antispam-type-${value}`)?.checked),
      },
    };
  }
  if (key === 'related') {
    return {
      data: {
        cancel_top_pin: document.getElementById('related-cancel-top-pin')?.checked || false,
        occupy_comment: document.getElementById('related-occupy-comment')?.checked || false,
        comment_message: collectMessage('related-comment'),
      },
    };
  }
  if (key === 'admin_access') {
    return {
      data: {
        mode: document.getElementById('admin-access-mode')?.value || 'all_admins',
      },
    };
  }
  if (key === 'nsfw') {
    return {
      data: {
        enabled: document.getElementById('nsfw-enabled')?.checked || false,
        sensitivity: document.getElementById('nsfw-sensitivity')?.value || 'medium',
        allow_miss: document.getElementById('nsfw-allow-miss')?.checked || false,
        notice_enabled: document.getElementById('nsfw-notice-enabled')?.checked || false,
        delay_delete_sec: Number(document.getElementById('nsfw-delay-delete-sec')?.value || 0),
      },
    };
  }
  if (key === 'lang') {
    return {
      data: {
        enabled: document.getElementById('lang-enabled')?.checked || false,
        allowed: (document.getElementById('lang-allowed')?.value || '').split(/[\n,]/).map((item) => item.trim()).filter(Boolean),
      },
    };
  }
  if (key === 'points') {
    return {
      data: {
        enabled: document.getElementById('points-enabled')?.checked || false,
        chat_points_enabled: document.getElementById('points-chat-enabled')?.checked || false,
        sign_command: document.getElementById('points-sign-command')?.value || '',
        query_command: document.getElementById('points-query-command')?.value || '',
        rank_command: document.getElementById('points-rank-command')?.value || '',
        sign_points: Number(document.getElementById('points-sign-points')?.value || 0),
        chat_points_per_message: Number(document.getElementById('points-chat-per-message')?.value || 0),
        min_text_length: Number(document.getElementById('points-min-text-length')?.value || 0),
        admin_adjust_enabled: document.getElementById('points-admin-adjust-enabled')?.checked || false,
      },
    };
  }
  if (key === 'activity') {
    return {
      data: {
        enabled: document.getElementById('activity-enabled')?.checked || false,
        today_command: document.getElementById('activity-today-command')?.value || '',
        month_command: document.getElementById('activity-month-command')?.value || '',
        total_command: document.getElementById('activity-total-command')?.value || '',
      },
    };
  }
  if (key === 'usdt') {
    return {
      data: {
        enabled: document.getElementById('usdt-enabled')?.checked || false,
        tier: document.getElementById('usdt-tier')?.value || 'best',
        show_query_message: document.getElementById('usdt-show-query')?.checked || false,
        show_calc_message: document.getElementById('usdt-show-calc')?.checked || false,
        alias_z: document.getElementById('usdt-alias-z')?.value || '',
        alias_w: document.getElementById('usdt-alias-w')?.value || '',
        alias_k: document.getElementById('usdt-alias-k')?.value || '',
        exchanges: ['binance', 'okx', 'htx'].filter((exchange) => document.getElementById(`usdt-exchange-${exchange}`)?.checked),
      },
    };
  }
  if (key === 'verified') {
    return {
      data: {
        enabled: document.getElementById('verified-enabled')?.checked || false,
      },
    };
  }
  if (key === 'schedule') {
    const items = [...document.querySelectorAll('[data-schedule-item]')].map((itemEl) => ({
      id: Number(itemEl.querySelector('[data-role="schedule-id"]')?.value || 0),
      next_at: Number(itemEl.querySelector('[data-role="schedule-next-at"]')?.value || 0),
      enabled: itemEl.querySelector('[data-role="schedule-enabled"]')?.checked || false,
      interval_sec: Math.max(60, Number(itemEl.querySelector('[data-role="schedule-interval-minutes"]')?.value || 1) * 60),
      text: itemEl.querySelector('[data-role="schedule-text"]')?.value || '',
      photo_file_id: itemEl.querySelector('[data-role="schedule-photo"]')?.value || '',
      buttons: collectButtonsFromList(itemEl.querySelector('.button-list')),
    }));
    return {
      data: {
        enabled: document.getElementById('schedule-enabled')?.checked || false,
        items,
      },
    };
  }
  if (key === 'autodelete') {
    const custom_rules = [...document.querySelectorAll('[data-delete-rule]')].map((ruleEl) => ({
      keyword: ruleEl.querySelector('[data-role="delete-rule-keyword"]')?.value || '',
      mode: ruleEl.querySelector('[data-role="delete-rule-mode"]')?.value || 'contains',
    }));
    const ad_sticker_ids = (document.getElementById('autodelete-sticker-ids')?.value || '')
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    return {
      data: {
        delete_system: document.getElementById('autodelete-delete-system')?.checked || false,
        delete_channel_mask: document.getElementById('autodelete-delete-channel-mask')?.checked || false,
        delete_links: document.getElementById('autodelete-delete-links')?.checked || false,
        delete_long: document.getElementById('autodelete-delete-long')?.checked || false,
        long_length: Number(document.getElementById('autodelete-long-length')?.value || 500),
        delete_videos: document.getElementById('autodelete-delete-videos')?.checked || false,
        delete_stickers: document.getElementById('autodelete-delete-stickers')?.checked || false,
        delete_forwarded: document.getElementById('autodelete-delete-forwarded')?.checked || false,
        delete_ad_stickers: document.getElementById('autodelete-delete-ad-stickers')?.checked || false,
        delete_archives: document.getElementById('autodelete-delete-archives')?.checked || false,
        delete_executables: document.getElementById('autodelete-delete-executables')?.checked || false,
        delete_notice_text: document.getElementById('autodelete-delete-notice-text')?.checked || false,
        delete_documents: document.getElementById('autodelete-delete-documents')?.checked || false,
        delete_mentions: document.getElementById('autodelete-delete-mentions')?.checked || false,
        delete_other_commands: document.getElementById('autodelete-delete-other-commands')?.checked || false,
        delete_qr: document.getElementById('autodelete-delete-qr')?.checked || false,
        delete_edited: document.getElementById('autodelete-delete-edited')?.checked || false,
        delete_member_emoji: document.getElementById('autodelete-delete-member-emoji')?.checked || false,
        delete_member_emoji_only: document.getElementById('autodelete-delete-member-emoji-only')?.checked || false,
        delete_external_reply: document.getElementById('autodelete-delete-external-reply')?.checked || false,
        delete_shared_contact: document.getElementById('autodelete-delete-shared-contact')?.checked || false,
        exclude_admins: document.getElementById('autodelete-exclude-admins')?.checked || false,
        custom_rules,
        ad_sticker_ids,
      },
    };
  }
  const raw = document.getElementById('module-json')?.value || '{}';
  try {
    return { data: JSON.parse(raw) };
  } catch {
    throw new Error('JSON 格式错误');
  }
}

function previewMessageFromCurrentForm() {
  const key = state.currentModuleKey;
  if (key === 'welcome') {
    return {
      message: collectMessage('welcome'),
      preview_context: { user: '新成员', userName: '新成员', group: state.summary?.group_title || '群组' },
    };
  }
  if (key === 'verify') {
    const mode = document.getElementById('verify-mode')?.value || 'join';
    return {
      message: collectMessage(`verify-${mode}`),
      preview_context: {
        user: '待验证成员',
        userName: '待验证成员',
        question: '3 + 9 = ?',
        group: state.summary?.group_title || '群组',
      },
    };
  }
  if (key === 'autoreply') {
    const firstRule = document.querySelector('[data-auto-rule]');
    if (!firstRule) return null;
    return {
      message: {
        text: firstRule.querySelector('[data-role="rule-reply-text"]')?.value || '',
        photo_file_id: firstRule.querySelector('[data-role="rule-photo"]')?.value || '',
        buttons: collectButtonsFromList(firstRule.querySelector('[data-button-list="rule"]')),
      },
      preview_context: { user: '群成员', userName: '群成员', group: state.summary?.group_title || '群组' },
    };
  }
  if (key === 'invite') {
    return {
      message: collectMessage('invite-notify'),
      preview_context: { user: '新成员', userName: '新成员', group: state.summary?.group_title || '群组' },
    };
  }
  if (key === 'related') {
    return {
      message: collectMessage('related-comment'),
      preview_context: { user: '频道关注者', userName: '频道关注者', group: state.summary?.group_title || '群组' },
    };
  }
  if (key === 'autowarn') {
    return {
      message: { text: document.getElementById('autowarn-text')?.value || '', photo_file_id: '', buttons: [] },
      preview_context: { user: '被警告成员', userName: '被警告成员', count: 2, limit: Number(document.getElementById('autowarn-limit')?.value || 3), group: state.summary?.group_title || '群组' },
    };
  }
  if (key === 'schedule') {
    const firstItem = document.querySelector('[data-schedule-item]');
    if (!firstItem) return null;
    return {
      message: {
        text: firstItem.querySelector('[data-role="schedule-text"]')?.value || '',
        photo_file_id: firstItem.querySelector('[data-role="schedule-photo"]')?.value || '',
        buttons: collectButtonsFromList(firstItem.querySelector('.button-list')),
      },
      preview_context: { user: '定时消息预览成员', userName: '定时消息预览成员', group: state.summary?.group_title || '群组' },
    };
  }
  return null;
}

function renderPreviewFallback() {
  const meta = moduleMeta(state.currentModuleKey) || {};
  const summary = summaryModule(state.currentModuleKey) || {};
  return `
    <div class="preview-chat">
      <div class="preview-phone">
        <div class="bubble">
          <div class="bubble-text"><strong>${escapeHtml(meta.label || '')}</strong><br/>${escapeHtml(summary.summary || '暂无可预览内容')}</div>
        </div>
      </div>
    </div>`;
}

function renderPreviewCard(preview) {
  const rows = Array.isArray(preview?.rows) ? preview.rows : [];
  const textHtml = preview?.text_html || '<span class="subtle">暂无文字</span>';
  return `
    <div class="preview-chat">
      <div class="preview-phone">
        <div class="bubble">
          ${preview?.photo_file_id ? `<div class="bubble-media">图片：${escapeHtml(preview.photo_file_id)}</div>` : ''}
          <div class="bubble-text">${textHtml}</div>
          ${rows.length ? `<div class="preview-actions">${rows.flatMap((row) => row || []).map((button) => `<div class="preview-button">${escapeHtml(button?.text || '按钮')}</div>`).join('')}</div>` : ''}
        </div>
      </div>
    </div>`;
}

async function refreshPreview() {
  const host = previewHost();
  if (!host) return;
  const preview = previewMessageFromCurrentForm();
  if (!preview) {
    state.previewHtml = renderPreviewFallback();
    host.innerHTML = state.previewHtml;
    return;
  }
  const requestId = ++previewRequestId;
  try {
    const rendered = await api('/api/web/render-preview', {
      method: 'POST',
      body: JSON.stringify(preview),
    });
    if (requestId !== previewRequestId) return;
    state.previewHtml = renderPreviewCard(rendered || {});
  } catch {
    if (requestId !== previewRequestId) return;
    state.previewHtml = renderPreviewFallback();
  }
  host.innerHTML = state.previewHtml || renderPreviewFallback();
}

function queuePreviewRender() {
  if (!state.me) return;
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => {
    void refreshPreview();
  }, 120);
}

function renderPreview() {
  return state.previewHtml || renderPreviewFallback();
}

async function saveCurrentModule() {
  try {
    const payload = collectPayload();
    state.modulePayload = {
      ...(state.modulePayload || {}),
      data: cloneData(payload.data),
    };
    state.modulePayload = await api(`/api/web/groups/${state.currentGroupId}/module/${state.currentModuleKey}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    state.summary = await api(`/api/web/groups/${state.currentGroupId}/summary`);
    state.previewHtml = '';
    state.notice = '\u4fdd\u5b58\u6210\u529f';
    state.noticeType = 'ok';
    render();
  } catch (error) {
    showNotice(humanizeErrorMessage(error.message, '保存失败'), 'error');
  }
}

function appendButton(listEl, button = {}) {
  if (!listEl) return;
  listEl.insertAdjacentHTML('beforeend', buttonItemHtml(button));
}

function nextModerationRuleId() {
  moderationRuleSeq += 1;
  return `mod-rule-${Date.now()}-${moderationRuleSeq}`;
}

function collectModerationRules(kind) {
  return [...document.querySelectorAll(`[data-moderation-rule="${kind}"]`)].map((ruleEl) => {
    const durationField = ruleEl.querySelector('[data-role="mod-rule-duration"]');
    const payload = {
      id: ruleEl.querySelector('[data-role="mod-rule-id"]')?.value || '',
      keyword: ruleEl.querySelector('[data-role="mod-rule-keyword"]')?.value || '',
      mode: ruleEl.querySelector('[data-role="mod-rule-mode"]')?.value || 'contains',
    };
    if (durationField) payload.duration_sec = Number(durationField.value || 0);
    return payload;
  });
}

function appendModerationRule(kind, rule = {}) {
  const config = moderationRuleConfig(kind);
  const container = document.getElementById(config?.containerId || '');
  if (!container) return;
  const empty = container.querySelector('.subtle');
  if (empty) empty.remove();
  container.insertAdjacentHTML('beforeend', moderationRuleHtml(kind, { ...rule, id: rule.id || nextModerationRuleId() }));
}

function syncModerationRuleEmptyState(kind) {
  const config = moderationRuleConfig(kind);
  const container = document.getElementById(config?.containerId || '');
  if (!container || !config) return;
  const hasRule = Boolean(container.querySelector(`[data-moderation-rule="${kind}"]`));
  const empty = container.querySelector('.subtle');
  if (hasRule && empty) {
    empty.remove();
    return;
  }
  if (!hasRule && !empty) {
    container.insertAdjacentHTML('beforeend', `<div class="subtle">${escapeHtml(config.emptyText)}</div>`);
  }
}

function appendAutoRule() {
  const container = document.getElementById('auto-rule-list');
  if (!container) return;
  const empty = container.querySelector('.subtle');
  if (empty) empty.remove();
  container.insertAdjacentHTML('beforeend', autoReplyRuleHtml({ keyword: '', mode: 'contains', enabled: true, reply_text: '', photo_file_id: '', buttons: [] }));
}

function nextScheduleItemKey() {
  scheduleItemSeq += 1;
  return `schedule-${Date.now()}-${scheduleItemSeq}`;
}

function appendScheduleItem(item = {}) {
  const container = document.getElementById('schedule-item-list');
  if (!container) return;
  const empty = container.querySelector('.subtle');
  if (empty) empty.remove();
  container.insertAdjacentHTML('beforeend', scheduleItemHtml(item, nextScheduleItemKey()));
}

function syncScheduleEmptyState() {
  const container = document.getElementById('schedule-item-list');
  if (!container) return;
  const hasItem = Boolean(container.querySelector('[data-schedule-item]'));
  const empty = container.querySelector('.subtle');
  if (hasItem && empty) {
    empty.remove();
    return;
  }
  if (!hasItem && !empty) {
    container.insertAdjacentHTML('beforeend', '<div class="subtle">暂无定时消息</div>');
  }
}

function appendDeleteRule(rule = {}) {
  const container = document.getElementById('delete-rule-list');
  if (!container) return;
  const empty = container.querySelector('.subtle');
  if (empty) empty.remove();
  container.insertAdjacentHTML('beforeend', deleteRuleHtml(rule));
}

function syncDeleteRuleEmptyState() {
  const container = document.getElementById('delete-rule-list');
  if (!container) return;
  const hasRule = Boolean(container.querySelector('[data-delete-rule]'));
  const empty = container.querySelector('.subtle');
  if (hasRule && empty) {
    empty.remove();
    return;
  }
  if (!hasRule && !empty) {
    container.insertAdjacentHTML('beforeend', '<div class="subtle">暂无自定义规则</div>');
  }
}

function syncRuleEmptyState() {
  const container = document.getElementById('auto-rule-list');
  if (!container) return;
  const hasRule = Boolean(container.querySelector('[data-auto-rule]'));
  const empty = container.querySelector('.subtle');
  if (hasRule && empty) {
    empty.remove();
    return;
  }
  if (!hasRule && !empty) {
    container.insertAdjacentHTML('beforeend', '<div class="subtle">\u6682\u65e0\u89c4\u5219</div>');
  }
}

function render() {
  if (!state.me) {
    renderLogin();
    return;
  }
  renderShell();
  queuePreviewRender();
}

async function boot() {
  try {
    await loadBootstrap();
    await loadSession();
  } catch (error) {
    state.notice = error.message || 'boot failed';
    state.noticeType = 'error';
  }
  render();
}

document.addEventListener('change', async (event) => {
  if (event.target.id === 'group-select') {
    clearNotice();
    await loadGroup(event.target.value, state.currentModuleKey);
    return;
  }
  if (state.me) {
    clearNotice();
    updatePreview();
  }
});

document.addEventListener('input', () => {
  if (state.me) {
    clearNotice();
    updatePreview();
  }
});

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'local-debug-login') {
    await localDebugLogin();
    return;
  }
  if (action === 'logout') {
    await api('/api/web/auth/logout', { method: 'POST', body: '{}' });
    state.me = null;
    state.summary = null;
    state.modulePayload = null;
    clearNotice();
    render();
    return;
  }
  if (action === 'select-module') {
    clearNotice();
    await loadModule(button.dataset.module);
    return;
  }
  if (action === 'save-module') {
    await saveCurrentModule();
    return;
  }
  if (action === 'reload-module') {
    clearNotice();
    await loadGroup(state.currentGroupId, state.currentModuleKey);
    return;
  }
  if (action === 'add-button') {
    const list = button.closest('.message-editor, .rule-card')?.querySelector('.button-list');
    appendButton(list, { text: '', type: 'url', value: '', row: 0 });
    updatePreview();
    return;
  }
  if (action === 'remove-button') {
    button.closest('[data-button-item]')?.remove();
    updatePreview();
    return;
  }
  if (action === 'add-rule') {
    appendAutoRule();
    syncRuleEmptyState();
    updatePreview();
    return;
  }
  if (action === 'add-autoban-rule') {
    appendModerationRule('autoban', { keyword: '', mode: 'contains', duration_sec: 0 });
    syncModerationRuleEmptyState('autoban');
    updatePreview();
    return;
  }
  if (action === 'remove-autoban-rule') {
    button.closest('[data-moderation-rule="autoban"]')?.remove();
    syncModerationRuleEmptyState('autoban');
    updatePreview();
    return;
  }
  if (action === 'add-automute-rule') {
    appendModerationRule('automute', { keyword: '', mode: 'contains', duration_sec: 60 });
    syncModerationRuleEmptyState('automute');
    updatePreview();
    return;
  }
  if (action === 'remove-automute-rule') {
    button.closest('[data-moderation-rule="automute"]')?.remove();
    syncModerationRuleEmptyState('automute');
    updatePreview();
    return;
  }
  if (action === 'add-autowarn-rule') {
    appendModerationRule('autowarn', { keyword: '', mode: 'contains' });
    syncModerationRuleEmptyState('autowarn');
    updatePreview();
    return;
  }
  if (action === 'remove-autowarn-rule') {
    button.closest('[data-moderation-rule="autowarn"]')?.remove();
    syncModerationRuleEmptyState('autowarn');
    updatePreview();
    return;
  }
  if (action === 'remove-rule') {
    button.closest('[data-auto-rule]')?.remove();
    syncRuleEmptyState();
    updatePreview();
    return;
  }
  if (action === 'add-schedule-item') {
    appendScheduleItem({ enabled: true, interval_sec: 3600, next_at: 0, text: '', photo_file_id: '', buttons: [] });
    syncScheduleEmptyState();
    updatePreview();
    return;
  }
  if (action === 'remove-schedule-item') {
    button.closest('[data-schedule-item]')?.remove();
    syncScheduleEmptyState();
    updatePreview();
    return;
  }
  if (action === 'add-delete-rule') {
    appendDeleteRule({ keyword: '', mode: 'contains' });
    syncDeleteRuleEmptyState();
    return;
  }
  if (action === 'remove-delete-rule') {
    button.closest('[data-delete-rule]')?.remove();
    syncDeleteRuleEmptyState();
    return;
  }
});

boot();
