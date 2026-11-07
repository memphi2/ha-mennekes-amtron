/* Offline behavioral tests of the shipped app UI, without a browser or network. */
'use strict';
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const directory = path.join(__dirname, '../custom_components/mennekes_amtron_rtu/device');
const html = readFileSync(path.join(directory, 'app_settings.html'), 'utf8');
const source = readFileSync(path.join(directory, 'app_settings.js'), 'utf8');

class Element {
  constructor() { this._value = ''; this.checked = false; this.disabled = false; this.hidden = false; this.required = false; this.textContent = ''; this.dataset = {}; this.listeners = {}; this.options = []; this.valid = true; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  async emit(type) { return this.listeners[type]?.({ target: this, preventDefault() {} }); }
  replaceChildren(...options) { this.options = options; }
  add(option) { this.options.push(option); }
  reportValidity() { return this.valid; }
}
const baseSettings = {
  connection_type: 'rtu_over_tcp', host: '192.0.2.10', tcp_port: 502,
  device_id: 50, baudrate: '57600', frame: '8n2', tcp_authentication: false, tcp_auth_password_set: false,
};
const detected = {
  adapter_token: 'opaque-preview', model: 'RS485 TO POE ETH (B)', firmware: '1.523', identifier: 'synthetic',
  tcp_port: 502, baudrate: 9600, frame: '8e1', connection_type: 'rtu_over_tcp', tcp_authentication: false, can_configure: true,
};
const result = (data, ok = true) => ({ ok, json: async () => data });
const settingsResponse = (settings = baseSettings, write_blocked = false) => result({ settings, adapters: [], revision: 'revision-1', csrf: 'csrf-proof', write_blocked });
const tick = () => new Promise(resolve => setImmediate(resolve));

async function harness(settings = baseSettings, language = 'en') {
  const elements = new Map();
  const translated = [];
  for (const match of html.matchAll(/<[a-z][^>]*>/g)) {
    const id = match[0].match(/\bid="([^"]+)"/);
    const key = match[0].match(/data-i18n="([^"]+)"/);
    const element = new Element();
    element.hidden = /\bhidden\b/.test(match[0]);
    element.disabled = /\bdisabled\b/.test(match[0]);
    if (id) elements.set(id[1], element);
    if (key) { element.dataset.i18n = key[1]; translated.push(element); }
  }
  for (const select of html.matchAll(/<select id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
    for (const option of select[2].matchAll(/<option(?: value="([^"]+)")?[^>]*>([^<]*)<\/option>/g)) {
      elements.get(select[1]).options.push({ value: option[1] ?? option[2] });
    }
  }
  const responses = [settingsResponse(settings)];
  const calls = [];
  const context = vm.createContext({
    document: {
      documentElement: { lang: language },
      getElementById(id) { assert.ok(elements.has(id), `Unknown HTML element: ${id}`); return elements.get(id); },
      querySelectorAll() { return translated; },
    },
    Option: class { constructor(label, value) { this.text = label; this.value = value; } },
    fetch: async (url, options = {}) => {
      calls.push({ url, ...options, payload: options.body ? JSON.parse(options.body) : undefined });
      assert.ok(responses.length, `Unexpected request: ${url}`);
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return typeof response === 'function' ? await response() : response;
    },
  });
  vm.runInContext(source, context, { filename: 'app_settings.js' });
  await tick();
  return { el: id => elements.get(id), responses, calls, context, translated, evaluate: code => vm.runInContext(code, context) };
}

async function detect(ui, values = detected) {
  ui.responses.push(result(values));
  await ui.el('detect_adapter').emit('click');
}

for (const language of ['en', 'de', 'fr', 'it', 'nl']) {
  test(`all visible UI strings and errors are available in ${language}`, async () => {
    const ui = await harness(baseSettings, language);
    assert.equal(ui.el('connection_form').hidden, false);
    for (const element of ui.translated) {
      assert.equal(ui.evaluate(`typeof messages[language][${JSON.stringify(element.dataset.i18n)}]`), 'string');
      if (element.dataset.i18n !== 'loading') assert.ok(element.textContent.length > 0, element.dataset.i18n);
    }
    assert.equal(ui.evaluate('Object.keys(messages.en).every(key => typeof messages[language][key] === "string")'), true);
    assert.match(ui.el('auth_security')?.textContent || ui.evaluate('text("auth_security")'), /1\.523/);
    assert.equal(ui.evaluate('Object.values(messages[language]).some(value => /gateway/i.test(value))'), false);
  });
}

test('transport dropdown retains precisely three choices', () => {
  const select = html.match(/<select id="connection_type"[^>]*>([\s\S]*?)<\/select>/)[1];
  assert.deepEqual([...select.matchAll(/value="([^"]+)"/g)].map(match => match[1]), ['serial', 'rtu_over_tcp', 'modbus_tcp']);
});

test('manual login keeps a saved password only for the same endpoint and never pre-fills it', async () => {
  const ui = await harness({ ...baseSettings, tcp_authentication: true, tcp_auth_password_set: true });
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.equal(ui.el('tcp_auth_password').required, false);
  ui.el('tcp_auth_password').value = 'Demo123';
  ui.el('host').value = '192.0.2.11';
  await ui.el('host').emit('input');
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.equal(ui.el('tcp_auth_password').required, true);
  ui.el('tcp_auth_password').value = 'Demo123';
  ui.responses.push(result({ revision: 'revision-2' }));
  await ui.el('connection_form').emit('submit');
  assert.equal(ui.calls.at(-1).payload.tcp_auth_password, 'Demo123');
  assert.equal(ui.calls.at(-1).payload.adapter_token, undefined);
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.equal(ui.evaluate('savedSettings.tcp_auth_password'), undefined);
  assert.equal(ui.el('save').disabled, false);
  ui.el('tcp_auth_password').value = 'New123';
  ui.el('tcp_port').value = '1502';
  await ui.el('tcp_port').emit('input');
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.equal(ui.el('tcp_auth_password').required, true);
});

test('detection is read-only, applying is explicit, disabling login still asks for its old password', async () => {
  const ui = await harness({ ...baseSettings, connection_type: 'serial', serial_port: '/dev/fake' });
  ui.el('connection_type').value = 'rtu_over_tcp';
  await ui.el('connection_type').emit('change');
  await detect(ui, { ...detected, tcp_authentication: true });
  assert.equal(ui.calls.at(-1).url, './api/adapter');
  assert.deepEqual(ui.calls.at(-1).payload, { host: '192.0.2.10', revision: 'revision-1' });
  assert.equal(ui.calls.at(-1).headers['X-Amtron-CSRF'], 'csrf-proof');
  assert.equal(ui.el('apply_adapter').checked, false);
  assert.equal(ui.el('adapter_fields').disabled, true);
  assert.equal(ui.el('adapter_baudrate').value, '57600'); // Keep the wallbox's existing serial speed.
  ui.el('apply_adapter').checked = true;
  await ui.el('apply_adapter').emit('change');
  ui.el('tcp_authentication').checked = false;
  await ui.el('tcp_authentication').emit('change');
  assert.equal(ui.el('password_fields').hidden, false);
  assert.equal(ui.el('tcp_auth_password').required, true);
  ui.el('tcp_auth_password').value = 'Demo123';
  ui.responses.push(result({ revision: 'revision-2' }));
  await ui.el('connection_form').emit('submit');
  const sent = ui.calls.at(-1).payload;
  assert.equal(sent.adapter_token, 'opaque-preview');
  assert.equal(sent.adapter_baudrate, 57600);
  assert.equal(sent.adapter_frame, '8n2');
  assert.equal(sent.tcp_authentication, false);
  assert.equal(sent.tcp_auth_password, 'Demo123');
  assert.equal(ui.el('save').disabled, true);
  assert.equal(ui.el('adapter_preview').hidden, true);
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.match(ui.el('status').textContent, /old connection is stopped/);
  assert.match(ui.el('restart_hint').textContent, /stops the old app connection/);
  ui.responses.push(settingsResponse());
  await ui.el('reload').emit('click');
  assert.equal(ui.el('save').disabled, true);
  assert.match(ui.el('status').textContent, /Restart/);
});

test('protected/unsupported adapters keep manual connection available without an apply token', async () => {
  const ui = await harness();
  await detect(ui, { ...detected, can_configure: false });
  assert.equal(ui.el('adapter_unsupported').hidden, false);
  assert.equal(ui.el('apply_adapter').disabled, true);
  ui.responses.push(result({ revision: 'revision-2' }));
  await ui.el('connection_form').emit('submit');
  assert.equal(ui.calls.at(-1).payload.adapter_token, undefined);
  assert.equal(ui.el('save').disabled, false);
});

test('factory serial values display accurately while valid wallbox settings are suggested', async () => {
  const ui = await harness();
  await detect(ui, { ...detected, baudrate: 115200, frame: '8n1' });
  assert.equal(ui.el('adapter_baudrate').value, '57600');
  assert.equal(ui.el('adapter_frame').value, '8n2');
  assert.match(ui.el('adapter_current').textContent, /115200.*8N1/);
  assert.equal(ui.el('apply_adapter').checked, false);
  assert.equal(ui.calls.length, 2); // Only GET config and read-only detection.
});

test('detection preserves requested login, TCP port and mode until explicitly applied', async () => {
  const ui = await harness();
  ui.el('tcp_authentication').checked = true;
  await ui.el('tcp_authentication').emit('change');
  ui.el('tcp_port').value = '1502';
  await ui.el('tcp_port').emit('input');
  ui.el('connection_type').value = 'modbus_tcp';
  await ui.el('connection_type').emit('change');
  await detect(ui);
  assert.equal(ui.el('tcp_authentication').checked, true);
  assert.equal(ui.el('tcp_port').value, '1502');
  assert.equal(ui.el('connection_type').value, 'modbus_tcp');
  assert.equal(ui.el('apply_adapter').checked, false);
  assert.match(ui.el('auth_hint').textContent, /app connection only/);
  ui.el('apply_adapter').checked = true;
  await ui.el('apply_adapter').emit('change');
  assert.match(ui.el('auth_label').textContent, /on the adapter/);
  assert.match(ui.el('auth_hint').textContent, /both the adapter and this app/);
  ui.el('tcp_auth_password').value = 'Demo123';
  ui.responses.push(result({ revision: 'revision-2' }));
  await ui.el('connection_form').emit('submit');
  assert.equal(ui.calls.at(-1).payload.tcp_authentication, true);
  assert.equal(ui.calls.at(-1).payload.tcp_port, 1502);
  assert.equal(ui.calls.at(-1).payload.connection_type, 'modbus_tcp');
  assert.equal(ui.calls.at(-1).payload.adapter_token, 'opaque-preview');
});

test('fresh detection preserves edited serial settings but reload restores hardware defaults', async () => {
  const ui = await harness();
  await detect(ui);
  ui.el('adapter_baudrate').value = '19200';
  await ui.el('adapter_baudrate').emit('change');
  ui.el('adapter_frame').value = '8o1';
  await ui.el('adapter_frame').emit('change');
  await detect(ui);
  assert.equal(ui.el('adapter_baudrate').value, '19200');
  assert.equal(ui.el('adapter_frame').value, '8o1');
  assert.equal(ui.el('apply_adapter').checked, false);
  ui.responses.push(settingsResponse());
  await ui.el('reload').emit('click');
  await detect(ui, { ...detected, tcp_port: 1502, connection_type: 'modbus_tcp', tcp_authentication: true });
  assert.equal(ui.el('adapter_baudrate').value, '9600');
  assert.equal(ui.el('adapter_frame').value, '8e1');
  assert.equal(ui.el('tcp_port').value, '1502');
  assert.equal(ui.el('connection_type').value, 'modbus_tcp');
  assert.equal(ui.el('tcp_authentication').checked, true);
});

test('host changes invalidate both in-flight detection and previously issued proof', async () => {
  const ui = await harness();
  let resolve;
  ui.responses.push(() => new Promise(done => { resolve = done; }));
  const pending = ui.el('detect_adapter').emit('click');
  await tick();
  assert.equal(ui.el('save').disabled, true);
  ui.el('host').value = '192.0.2.99';
  await ui.el('host').emit('input');
  resolve(result(detected));
  await pending;
  assert.equal(ui.el('adapter_preview').hidden, true);
  assert.equal(ui.evaluate('adapter'), null);
  await detect(ui);
  ui.el('apply_adapter').checked = true;
  await ui.el('apply_adapter').emit('change');
  ui.el('host').value = '192.0.2.12';
  await ui.el('host').emit('input');
  assert.equal(ui.el('apply_adapter').checked, false);
  assert.equal(ui.el('adapter_fields').disabled, true);
});

test('failed detection does not force setup of generic LAN adapters', async () => {
  const ui = await harness();
  ui.responses.push(result({ error: 'gateway_unsupported' }, false));
  await ui.el('detect_adapter').emit('click');
  assert.equal(ui.el('error').hidden, false);
  assert.equal(ui.el('save').disabled, false);
  ui.responses.push(result({ revision: 'revision-2' }));
  await ui.el('connection_form').emit('submit');
  assert.equal(ui.calls.at(-1).payload.adapter_token, undefined);
});

for (const failure of ['adapter_save_uncertain', 'gateway_rollback_failed', 'lost_response']) {
  test(`uncertain physical save ${failure} requires reload and fresh recognition`, async () => {
    const ui = await harness();
    await detect(ui);
    ui.el('apply_adapter').checked = true;
    await ui.el('apply_adapter').emit('change');
    ui.responses.push(failure === 'lost_response' ? new Error('connection gone') : result({ error: failure }, false));
    await ui.el('connection_form').emit('submit');
    assert.equal(ui.el('save').disabled, true);
    assert.equal(ui.el('apply_adapter').checked, false);
    assert.equal(ui.evaluate('adapter'), null);
    const before = ui.calls.length;
    await ui.el('connection_form').emit('submit');
    assert.equal(ui.calls.length, before);
    const blocked = failure === 'adapter_save_uncertain';
    ui.responses.push(settingsResponse(baseSettings, blocked));
    await ui.el('reload').emit('click');
    assert.equal(ui.el('save').disabled, true);
    assert.match(ui.el('error').textContent, blocked ? /previous save may still complete/ : /could not be confirmed together/);
    await detect(ui);
    assert.equal(ui.el('save').disabled, blocked);
    if (blocked) assert.match(ui.el('error').textContent, /previous save may still complete/);
  });
}

test('an unconfirmed ordinary save stays blocked after reopening or detecting the adapter', async () => {
  const ui = await harness();
  ui.responses.push(result({ error: 'options_save_uncertain' }, false));
  await ui.el('connection_form').emit('submit');
  assert.equal(ui.el('save').disabled, true);
  assert.match(ui.el('error').textContent, /previous save may still complete/);
  ui.responses.push(settingsResponse(baseSettings, true));
  await ui.el('reload').emit('click');
  await detect(ui);
  assert.equal(ui.el('save').disabled, true);
  assert.match(ui.el('error').textContent, /previous save may still complete/);
});

test('rejected password clears proof and input without pretending the connection was saved', async () => {
  const ui = await harness();
  await detect(ui);
  ui.el('apply_adapter').checked = true;
  await ui.el('apply_adapter').emit('change');
  ui.el('tcp_auth_password').value = 'Demo123';
  ui.responses.push(result({ error: 'gateway_authentication_failed' }, false));
  await ui.el('connection_form').emit('submit');
  assert.match(ui.el('error').textContent, /rejected/);
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.equal(ui.evaluate('adapter'), null);
  assert.equal(ui.evaluate('revision'), 'revision-1');
});

test('USB saves exclude every LAN credential and adapter setup field', async () => {
  const ui = await harness({ ...baseSettings, tcp_authentication: true });
  await detect(ui);
  ui.el('tcp_auth_password').value = 'Demo123';
  ui.el('connection_type').value = 'serial';
  await ui.el('connection_type').emit('change');
  assert.equal(ui.el('tcp_auth_password').value, '');
  assert.equal(ui.el('password_fields').hidden, true);
  ui.responses.push(result({ revision: 'revision-2' }));
  await ui.el('connection_form').emit('submit');
  assert.deepEqual(Object.keys(ui.calls.at(-1).payload).sort(), ['baudrate', 'connection_type', 'device_id', 'frame', 'revision', 'serial_port']);
});
