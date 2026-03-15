import test from 'node:test';
import assert from 'node:assert/strict';

import {
  attachWithAutoStart,
  browserCommandWithAutoStart,
  buildAttachPayload,
  classifyOpenSignal,
  createLumonPlugin,
  createLumonController,
  extractSessionId,
  isAttachRelevantEvent,
  loadPluginConfig,
  shouldOpenForEvent,
} from '../lib/lumonPluginCore.js';

function okHealthResponse() {
  return {
    ok: true,
    json: async () => ({
      status: 'ok',
      protocol_version: '1.3.1',
      runtime_version: '2026-03-16-browser-flow-v3',
    }),
  };
}

function okFrontendResponse() {
  return {
    ok: true,
    text: async () => '<!doctype html><html><head><title>Lumon</title></head><body><div id="root"></div></body></html>',
  };
}

test('loadPluginConfig defaults to observe_only', () => {
  const config = loadPluginConfig({});
  assert.equal(config.webMode, 'observe_only');
  assert.equal(config.autoDelegate, false);
  assert.equal(config.openPolicy, 'browser_or_intervention');
  assert.equal(config.enablePromptSteering, true);
  assert.equal(config.forceDelegateOnBrowserSignal, false);
});

test('extractSessionId finds nested session ids', () => {
  assert.equal(extractSessionId({ type: 'session.created', session: { id: 'sess_001' } }), 'sess_001');
  assert.equal(extractSessionId({ type: 'message.updated', payload: { session_id: 'sess_002' } }), 'sess_002');
  assert.equal(extractSessionId({ type: 'session.created', properties: { info: { id: 'sess_003' } } }), 'sess_003');
  assert.equal(extractSessionId({ type: 'session.status', properties: { sessionID: 'sess_004' } }), 'sess_004');
  assert.equal(extractSessionId({ type: 'message.part.updated', properties: { part: { sessionID: 'sess_005' } } }), 'sess_005');
  assert.equal(
    extractSessionId({ type: 'message.updated', properties: { info: { id: 'msg_001', sessionID: 'sess_006' } } }),
    'sess_006',
  );
});

test('isAttachRelevantEvent filters to session/message/tool/permission events', () => {
  assert.equal(isAttachRelevantEvent({ type: 'session.created' }), true);
  assert.equal(isAttachRelevantEvent({ type: 'message.updated' }), true);
  assert.equal(isAttachRelevantEvent({ type: 'tool.execute.after' }), true);
  assert.equal(isAttachRelevantEvent({ type: 'ui.focus' }), false);
});

test('shouldOpenForEvent opens only for browser/intervention events by default', () => {
  assert.equal(
    shouldOpenForEvent({ type: 'tool.execute.after', tool: { name: 'webfetch', url: 'https://example.com' } }),
    true,
  );
  assert.equal(
    shouldOpenForEvent({ type: 'permission.asked', message: 'Need approval to continue' }),
    true,
  );
  assert.equal(
    shouldOpenForEvent({ type: 'message.updated', content: 'Read the local repository docs' }),
    false,
  );
  assert.equal(
    shouldOpenForEvent({ type: 'session.diff', diff: 'status blocked pending approval' }),
    false,
  );
});

test('classifyOpenSignal distinguishes browser and intervention signals', () => {
  assert.equal(
    classifyOpenSignal({ type: 'tool.execute.after', tool: { name: 'webfetch', url: 'https://example.com' } }),
    'browser',
  );
  assert.equal(
    classifyOpenSignal({ type: 'permission.asked', message: 'Need approval to continue' }),
    'intervention',
  );
  assert.equal(
    classifyOpenSignal({ type: 'message.updated', content: 'Read the local repository docs' }),
    null,
  );
  assert.equal(
    classifyOpenSignal({ type: 'session.diff', diff: 'status blocked pending approval' }),
    null,
  );
});

test('buildAttachPayload uses defaults and event session id', () => {
  const payload = buildAttachPayload({
    event: { type: 'session.created', session: { id: 'sess_attach' } },
    config: { webMode: 'observe_only', autoDelegate: false, frontendOrigin: 'http://127.0.0.1:5173' },
    directory: '/repo',
  });
  assert.deepEqual(payload, {
    observed_session_id: 'sess_attach',
    project_directory: '/repo',
    web_mode: 'observe_only',
    auto_delegate: false,
    frontend_origin: 'http://127.0.0.1:5173',
  });
});

test('attachWithAutoStart starts app and retries on recoverable attach failure', async () => {
  const calls = [];
  let attempt = 0;
  const response = await attachWithAutoStart({
    attach: async () => {
      attempt += 1;
      calls.push(`attach:${attempt}`);
      if (attempt === 1) throw new Error('fetch failed');
      return { session_id: 'sess_1', open_url: 'http://127.0.0.1:5173/?session=sess_1', already_attached: false };
    },
    startApp: async () => calls.push('start-app'),
    waitForHealth: async () => calls.push('wait-health'),
    payload: { observed_session_id: 'sess_1' },
    config: { disableAutoStart: false, webMode: 'observe_only' },
    log: async () => calls.push('log'),
  });

  assert.equal(response.session_id, 'sess_1');
  assert.deepEqual(calls, ['attach:1', 'log', 'start-app', 'wait-health', 'attach:2']);
});

test('browserCommandWithAutoStart starts app and retries on recoverable command failure', async () => {
  const calls = [];
  let attempt = 0;
  const response = await browserCommandWithAutoStart({
    command: async () => {
      attempt += 1;
      calls.push(`command:${attempt}`);
      if (attempt === 1) throw new Error('fetch failed');
      return { command_id: 'cmd_1', status: 'success' };
    },
    startApp: async () => calls.push('start-app'),
    waitForHealth: async () => calls.push('wait-health'),
    payload: { command_id: 'cmd_1', command: 'status' },
    config: { disableAutoStart: false, webMode: 'delegate_playwright' },
    log: async () => calls.push('log'),
  });

  assert.equal(response.command_id, 'cmd_1');
  assert.deepEqual(calls, ['command:1', 'log', 'start-app', 'wait-health', 'command:2']);
});

test('attachWithAutoStart retries on recoverable HTTP 503 failures', async () => {
  const calls = [];
  let attempt = 0;
  const response = await attachWithAutoStart({
    attach: async () => {
      attempt += 1;
      calls.push(`attach:${attempt}`);
      if (attempt === 1) throw new Error('attach failed (503): warming up');
      return { session_id: 'sess_503', open_url: 'http://127.0.0.1:5173/?session=sess_503', already_attached: false };
    },
    startApp: async () => calls.push('start-app'),
    waitForHealth: async () => calls.push('wait-health'),
    payload: { observed_session_id: 'sess_503' },
    config: { disableAutoStart: false, webMode: 'observe_only' },
    log: async () => calls.push('log'),
  });

  assert.equal(response.session_id, 'sess_503');
  assert.deepEqual(calls, ['attach:1', 'log', 'start-app', 'wait-health', 'attach:2']);
});

test('browserCommandWithAutoStart retries on recoverable HTTP 503 failures', async () => {
  const calls = [];
  let attempt = 0;
  const response = await browserCommandWithAutoStart({
    command: async () => {
      attempt += 1;
      calls.push(`command:${attempt}`);
      if (attempt === 1) throw new Error('browser command failed (503): delegate warming');
      return { command_id: 'cmd_503', status: 'success' };
    },
    startApp: async () => calls.push('start-app'),
    waitForHealth: async () => calls.push('wait-health'),
    payload: { command_id: 'cmd_503', command: 'open' },
    config: { disableAutoStart: false, webMode: 'delegate_playwright' },
    log: async () => calls.push('log'),
  });

  assert.equal(response.command_id, 'cmd_503');
  assert.deepEqual(calls, ['command:1', 'log', 'start-app', 'wait-health', 'command:2']);
});

test('controller attaches once per session and only opens UI on interventions', async () => {
  const attaches = [];
  const opens = [];
  const controller = createLumonController({
    config: {
      webMode: 'observe_only',
      autoDelegate: false,
      openPolicy: 'browser_or_intervention',
      disableAutoStart: false,
    },
    attach: async (payload) => {
      attaches.push(payload);
      return {
        session_id: 'lumon_1',
        open_url: 'http://127.0.0.1:5173/?session_id=lumon_1',
        already_attached: attaches.length > 1,
      };
    },
    startApp: async () => {},
    waitForHealth: async () => {},
    openUrl: async (url) => opens.push(url),
    log: async () => {},
  });

  await controller.handleEvent({ type: 'session.created', session: { id: 'sess_1' } }, '/repo');
  await controller.handleEvent({ type: 'message.updated', session: { id: 'sess_1' }, content: 'Read local files' }, '/repo');
  await controller.handleEvent({ type: 'tool.execute.after', session: { id: 'sess_1' }, tool: { name: 'webfetch', url: 'https://example.com' } }, '/repo');
  await controller.handleEvent({ type: 'permission.asked', session: { id: 'sess_1' }, message: 'Need approval to continue' }, '/repo');

  assert.equal(attaches.length, 1);
  assert.deepEqual(opens, ['http://127.0.0.1:5173/?session_id=lumon_1']);
});

test('controller opens for browser signals with episode/cooldown gating', async (t) => {
  const originalDateNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  t.after(() => {
    Date.now = originalDateNow;
  });

  const opens = [];
  const controller = createLumonController({
    config: {
      webMode: 'observe_only',
      autoDelegate: false,
      openPolicy: 'browser_or_intervention',
      disableAutoStart: false,
      browserEpisodeGapMs: 25000,
      interventionEpisodeGapMs: 10000,
      reopenCooldownMs: 20000,
    },
    attach: async () => ({
      session_id: 'lumon_2',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_2',
      already_attached: false,
    }),
    startApp: async () => {},
    waitForHealth: async () => {},
    openUrl: async (url) => opens.push(url),
    log: async () => {},
  });

  await controller.handleEvent({ type: 'session.created', session: { id: 'sess_2' } }, '/repo');
  await controller.handleEvent({ type: 'tool.execute.after', session: { id: 'sess_2' }, tool: { name: 'webfetch', url: 'https://example.com' } }, '/repo');
  now += 5000;
  await controller.handleEvent({ type: 'tool.execute.after', session: { id: 'sess_2' }, tool: { name: 'webfetch', url: 'https://example.com/a' } }, '/repo');
  now += 65000;
  await controller.handleEvent({ type: 'tool.execute.after', session: { id: 'sess_2' }, tool: { name: 'webfetch', url: 'https://example.com/b' } }, '/repo');

  assert.deepEqual(opens, [
    'http://127.0.0.1:5173/?session_id=lumon_2',
    'http://127.0.0.1:5173/?session_id=lumon_2',
  ]);
});

test('controller can reopen faster for intervention episodes', async (t) => {
  const originalDateNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  t.after(() => {
    Date.now = originalDateNow;
  });

  const opens = [];
  const controller = createLumonController({
    config: {
      webMode: 'observe_only',
      autoDelegate: false,
      openPolicy: 'browser_or_intervention',
      disableAutoStart: false,
      browserEpisodeGapMs: 25000,
      interventionEpisodeGapMs: 10000,
      reopenCooldownMs: 20000,
    },
    attach: async () => ({
      session_id: 'lumon_3',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_3',
      already_attached: false,
    }),
    startApp: async () => {},
    waitForHealth: async () => {},
    openUrl: async (url) => opens.push(url),
    log: async () => {},
  });

  await controller.handleEvent({ type: 'session.created', session: { id: 'sess_3' } }, '/repo');
  await controller.handleEvent({ type: 'permission.asked', session: { id: 'sess_3' }, message: 'Need approval to continue' }, '/repo');
  now += 12000;
  await controller.handleEvent({ type: 'permission.asked', session: { id: 'sess_3' }, message: 'Need approval to continue' }, '/repo');

  assert.deepEqual(opens, [
    'http://127.0.0.1:5173/?session_id=lumon_3',
    'http://127.0.0.1:5173/?session_id=lumon_3',
  ]);
});

test('controller suppresses observer-driven browser reopen while tool-backed browser activity is recent', async (t) => {
  const originalDateNow = Date.now;
  let now = 30_000;
  Date.now = () => now;
  t.after(() => {
    Date.now = originalDateNow;
  });

  const opens = [];
  const commandActivity = new Map([
    ['sess_tool_active', { lastCommandAt: now - 500, lastStatus: 'success', lastReason: null }],
  ]);
  const controller = createLumonController({
    config: {
      webMode: 'observe_only',
      autoDelegate: false,
      openPolicy: 'browser_or_intervention',
      disableAutoStart: false,
      browserEpisodeGapMs: 25000,
      interventionEpisodeGapMs: 10000,
      reopenCooldownMs: 20000,
    },
    attach: async () => ({
      session_id: 'lumon_tool_active',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool_active',
      already_attached: false,
    }),
    startApp: async () => {},
    waitForHealth: async () => {},
    openUrl: async (url) => opens.push(url),
    log: async () => {},
    commandActivity,
  });

  await controller.handleEvent({ type: 'session.created', session: { id: 'sess_tool_active' } }, '/repo');
  await controller.handleEvent(
    { type: 'message.part.updated', session: { id: 'sess_tool_active' }, content: 'Browser showed new visible output.' },
    '/repo',
  );

  assert.deepEqual(opens, []);
});

test('createLumonPlugin exposes lumon_browser as a real custom tool', async () => {
  const plugin = await createLumonPlugin({
    $: undefined,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  assert.equal(typeof plugin.tool.lumon_browser.execute, 'function');
  assert.match(plugin.tool.lumon_browser.description, /interactive browser tasks/i);
});


test('chat.message steering resolves session ids from parts when output.message is missing them', async () => {
  const previous = process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING;
  process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING = 'true';
  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const output = {
    message: { id: 'msg_1', tools: { webfetch: true } },
    parts: [
      {
        id: 'part_1',
        sessionID: 'sess_from_part',
        messageID: 'msg_1',
        type: 'text',
        text: 'Open https://www.wikipedia.org, click the search box, type OpenAI, and stop before submitting.',
      },
    ],
  };

  await plugin['chat.message']({}, output);

  assert.equal(output.message.tools.lumon_browser, true);
  assert.equal(output.message.tools.webfetch, false);
  if (previous === undefined) {
    delete process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING;
  } else {
    process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING = previous;
  }
});

test('controller suppresses observer-driven browser open while an interactive prompt is pending tool execution', async (t) => {
  const originalDateNow = Date.now;
  let now = 10_000;
  Date.now = () => now;
  t.after(() => {
    Date.now = originalDateNow;
  });

  const opens = [];
  const pendingPromptSteering = new Map([['sess_pending', now + 15_000]]);
  const controller = createLumonController({
    config: {
      webMode: 'observe_only',
      autoDelegate: false,
      openPolicy: 'browser_or_intervention',
      disableAutoStart: false,
      browserEpisodeGapMs: 25_000,
      interventionEpisodeGapMs: 10_000,
      reopenCooldownMs: 20_000,
    },
    attach: async () => ({
      session_id: 'lumon_pending',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_pending',
      already_attached: false,
    }),
    startApp: async () => {},
    waitForHealth: async () => {},
    openUrl: async (url) => opens.push(url),
    log: async () => {},
    pendingPromptSteering,
  });

  await controller.handleEvent({ type: 'session.created', session: { id: 'sess_pending' } }, '/repo');
  await controller.handleEvent(
    { type: 'tool.execute.after', session: { id: 'sess_pending' }, tool: { name: 'webfetch', url: 'https://example.com' } },
    '/repo',
  );

  assert.deepEqual(opens, []);
});

test('chat.message steers interactive browser prompts toward lumon_browser instead of webfetch', async () => {
  const previous = process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING;
  process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING = 'true';
  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const output = {
    message: { id: 'msg_1', sessionID: 'sess_1', tools: { webfetch: true } },
    parts: [
      {
        id: 'part_1',
        sessionID: 'sess_1',
        messageID: 'msg_1',
        type: 'text',
        text: 'Open https://www.wikipedia.org, click the search box, type OpenAI, and stop before submitting.',
      },
    ],
  };

  await plugin['chat.message']({ sessionID: 'sess_1' }, output);

  assert.equal(output.message.tools.lumon_browser, true);
  assert.equal(output.message.tools.webfetch, false);
  assert.equal(output.parts.length, 1);
  if (previous === undefined) {
    delete process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING;
  } else {
    process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING = previous;
  }
});

test('chat.message steers local approval and revisit prompts toward lumon_browser', async () => {
  const previous = process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING;
  process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING = 'true';
  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const scenarios = [
    'Open the local approval page, click submit, approve the intervention, and stop.',
    'Open the local trace page again, inspect it, and stop before submitting.',
  ];

  for (const [index, prompt] of scenarios.entries()) {
    const output = {
      message: { id: `msg_${index}`, sessionID: `sess_${index}`, tools: { webfetch: true } },
      parts: [
        {
          id: `part_${index}`,
          sessionID: `sess_${index}`,
          messageID: `msg_${index}`,
          type: 'text',
          text: prompt,
        },
      ],
    };

    await plugin['chat.message']({ sessionID: `sess_${index}` }, output);
    assert.equal(output.message.tools.lumon_browser, true);
    assert.equal(output.message.tools.webfetch, false);
  }

  if (previous === undefined) {
    delete process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING;
  } else {
    process.env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING = previous;
  }
});

test('tool.definition hardens lumon_browser guidance for the model', async () => {
  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const output = { description: '', parameters: {} };
  await plugin['tool.definition']({ toolID: 'lumon_browser' }, output);

  assert.match(output.description, /never narrate browser success/i);
});

test('lumon_browser auto-start launches backend and frontend directly before retrying', async (t) => {
  const shellCommands = [];
  const originalFetch = global.fetch;
  let browserCommandAttempts = 0;

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      browserCommandAttempts += 1;
      if (browserCommandAttempts === 1) {
        throw new Error('fetch failed');
      }
      return {
        ok: true,
        json: async () => ({
          command_id: 'cmd_open',
          command: 'open',
          status: 'success',
          summary_text: 'Opened page.',
        }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
  });

  const shellHelper = async (strings, ...values) => {
    shellCommands.push(String.raw({ raw: strings }, ...values));
  };

  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const result = await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_open', command: 'open', url: 'https://www.wikipedia.org' },
    { sessionID: 'sess_123', directory: '/repo', metadata: () => {} },
  );

  const parsed = JSON.parse(result);
  assert.equal(parsed.status, 'success');
  assert.equal(browserCommandAttempts, 2);
  assert.ok(shellCommands.some((entry) => entry.includes('./scripts/start_demo_backend.sh')));
  assert.ok(shellCommands.some((entry) => entry.includes('./scripts/start_demo_frontend.sh')));
});

test('intervention episode starts the frontend before opening the Lumon UI when backend is already up', async (t) => {
  const shellCommands = [];
  const originalFetch = global.fetch;
  const originalTimeout = process.env.LUMON_PLUGIN_STARTUP_TIMEOUT_MS;
  let frontendStarted = false;
  process.env.LUMON_PLUGIN_STARTUP_TIMEOUT_MS = '50';

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/observe/opencode')) {
      return {
        ok: true,
        json: async () => ({
          session_id: 'lumon_1',
          open_url: 'http://127.0.0.1:5173/?session_id=lumon_1',
          already_attached: false,
        }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          protocol_version: '1.3.1',
          runtime_version: '2026-03-16-browser-flow-v3',
        }),
      };
    }
    if (asText === 'http://127.0.0.1:5173') {
      return frontendStarted ? okFrontendResponse() : { ok: false, text: async () => '' };
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
    if (originalTimeout === undefined) {
      delete process.env.LUMON_PLUGIN_STARTUP_TIMEOUT_MS;
    } else {
      process.env.LUMON_PLUGIN_STARTUP_TIMEOUT_MS = originalTimeout;
    }
  });

  const shellHelper = async (strings, ...values) => {
    const command = String.raw({ raw: strings }, ...values);
    shellCommands.push(command);
    if (command.includes('./scripts/start_demo_frontend.sh')) {
      frontendStarted = true;
    }
  };

  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.event({
    event: { type: 'session.created', session: { id: 'sess_browser' } },
  });
  await plugin.event({
    event: {
      type: 'permission.asked',
      session: { id: 'sess_browser' },
      message: 'Need approval to continue',
    },
  });

  assert.ok(shellCommands.some((entry) => entry.includes('./scripts/start_demo_frontend.sh')));
  assert.ok(shellCommands.some((entry) => entry.includes('open "http://127.0.0.1:5173/?session_id=lumon_1"')));
});


test('lumon_browser suppresses observer-driven open while begin_task is still in flight', async (t) => {
  const shellCommands = [];
  const originalFetch = global.fetch;
  let resolveCommand;
  const commandPromise = new Promise((resolve) => {
    resolveCommand = resolve;
  });

  global.fetch = async (url, options) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/observe/opencode')) {
      return {
        ok: true,
        json: async () => ({
          session_id: 'lumon_tool',
          open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
          already_attached: false,
        }),
      };
    }
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      return {
        ok: true,
        json: async () => commandPromise,
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
  });

  const shellHelper = async (strings, ...values) => {
    shellCommands.push(String.raw({ raw: strings }, ...values));
  };

  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin['chat.message'](
    { sessionID: 'sess_inflight' },
    {
      message: { id: 'msg_1', sessionID: 'sess_inflight', tools: { webfetch: true } },
      parts: [
        {
          id: 'part_1',
          sessionID: 'sess_inflight',
          messageID: 'msg_1',
          type: 'text',
          text: 'Open https://www.wikipedia.org, click the search box, type OpenAI, and stop before submitting.',
        },
      ],
    },
  );

  await plugin.event({ event: { type: 'session.created', session: { id: 'sess_inflight' } } });
  const inFlight = plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_begin', command: 'begin_task', task_text: 'Open Wikipedia' },
    { sessionID: 'sess_inflight', directory: '/repo', metadata: () => {} },
  );

  await new Promise((resolve) => setTimeout(resolve, 0));
  await plugin.event({
    event: {
      type: 'message.part.updated',
      session: { id: 'sess_inflight' },
      content: 'Browser showed new visible output.',
    },
  });

  const openCommandsBeforeResolve = shellCommands.filter((entry) =>
    entry.includes('open "http://127.0.0.1:5173/?session_id=lumon_tool"'),
  );
  assert.equal(openCommandsBeforeResolve.length, 0);

  resolveCommand({
    command_id: 'cmd_begin',
    command: 'begin_task',
    status: 'partial',
    summary_text: 'Delegate prepared and waiting for navigation.',
    reason: 'awaiting_first_navigation',
    open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
    evidence: null,
  });
  await inFlight;
});

test('lumon_browser opens the UI only when a command returns frame evidence', async (t) => {
  const shellCommands = [];
  const originalFetch = global.fetch;

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      return {
        ok: true,
        json: async () => ({
          command_id: 'cmd_begin',
          command: 'begin_task',
          status: 'partial',
          summary_text: 'Lumon prepared the live browser delegate for this task.',
          reason: 'awaiting_first_navigation',
          open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
          evidence: null,
        }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
  });

  const shellHelper = async (strings, ...values) => {
    shellCommands.push(String.raw({ raw: strings }, ...values));
  };

  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const result = await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_begin', command: 'begin_task', task_text: 'Open Wikipedia' },
    { sessionID: 'sess_123', directory: '/repo', metadata: () => {} },
  );

  const parsed = JSON.parse(result);
  assert.equal(parsed.status, 'partial');
  assert.equal(shellCommands.some((entry) => entry.includes('open "http://127.0.0.1:5173/?session_id=lumon_tool"')), false);
});

test('blocked command does not reopen the already-open Lumon UI for the same task', async (t) => {
  const shellCommands = [];
  const originalFetch = global.fetch;
  let callCount = 0;

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      callCount += 1;
      if (callCount === 1) {
        return {
          ok: true,
          json: async () => ({
            command_id: 'cmd_open',
            command: 'open',
            status: 'success',
            summary_text: 'Opened page.',
            open_url: 'http://127.0.0.1:5173/?session_id=lumon_blocked',
            evidence: { verified: true, frame_emitted: true },
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          command_id: 'cmd_click',
          command: 'click',
          status: 'blocked',
          reason: 'awaiting_approval',
          summary_text: 'Approval required.',
          open_url: 'http://127.0.0.1:5173/?session_id=lumon_blocked',
          checkpoint_id: 'chk_1',
          evidence: null,
        }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const shellHelper = async (strings, ...values) => {
    shellCommands.push(String.raw({ raw: strings }, ...values));
  };

  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_open', command: 'begin_task', task_text: 'Open a page' },
    { sessionID: 'sess_blocked', directory: '/repo', metadata: () => {} },
  );
  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_click', command: 'click', element_id: 'el_1' },
    { sessionID: 'sess_blocked', directory: '/repo', metadata: () => {} },
  );

  const openCommands = shellCommands.filter((entry) => entry.includes('open "http://127.0.0.1:5173/?session_id=lumon_blocked"'));
  assert.equal(openCommands.length, 1);
});

test('lumon_browser keeps a successful command successful when UI auto-open fails', async (t) => {
  const originalFetch = global.fetch;

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      return {
        ok: true,
        text: async () =>
          JSON.stringify({
            command_id: 'cmd_open',
            command: 'open',
            status: 'success',
            summary_text: 'Opened wikipedia.org.',
            open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
            evidence: { frame_emitted: true, verified: true, details: {} },
          }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          protocol_version: '1.3.1',
          runtime_version: '2026-03-16-browser-flow-v3',
        }),
      };
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {
      throw new Error('open failed');
    },
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const result = await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_open', command: 'open', url: 'https://www.wikipedia.org' },
    { sessionID: 'sess_1', directory: '/repo', metadata: () => {} },
  );

  const parsed = JSON.parse(result);
  assert.equal(parsed.status, 'success');
});

test('lumon_browser does not reopen the same Lumon session on later successful commands in one browser task', async (t) => {
  const shellCommands = [];
  const originalFetch = global.fetch;
  const commandResponses = {
    begin_task: {
      command_id: 'cmd_begin',
      command: 'begin_task',
      status: 'partial',
      summary_text: 'Delegate prepared and waiting for navigation.',
      reason: 'awaiting_first_navigation',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
      evidence: null,
    },
    open: {
      command_id: 'cmd_open',
      command: 'open',
      status: 'success',
      summary_text: 'Opened wikipedia.org.',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
      evidence: { frame_emitted: true },
    },
    type: {
      command_id: 'cmd_type',
      command: 'type',
      status: 'success',
      summary_text: 'Typed OpenAI into the search box.',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
      evidence: { frame_emitted: true },
    },
  };

  global.fetch = async (url, options) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      const body = JSON.parse(options.body);
      const response = commandResponses[body.command];
      if (!response) {
        throw new Error(`unexpected command ${body.command}`);
      }
      return {
        ok: true,
        json: async () => response,
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
  });

  const shellHelper = async (strings, ...values) => {
    shellCommands.push(String.raw({ raw: strings }, ...values));
  };

  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_begin', command: 'begin_task', task_text: 'Open Wikipedia and type OpenAI' },
    { sessionID: 'sess_tool', directory: '/repo', metadata: () => {} },
  );
  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_begin', command: 'open', url: 'https://www.wikipedia.org' },
    { sessionID: 'sess_tool', directory: '/repo', metadata: () => {} },
  );
  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_begin', command: 'type', element_id: 'search', text: 'OpenAI' },
    { sessionID: 'sess_tool', directory: '/repo', metadata: () => {} },
  );

  const openCommands = shellCommands.filter((entry) =>
    entry.includes('open "http://127.0.0.1:5173/?session_id=lumon_tool"'),
  );
  assert.equal(openCommands.length, 1);
});

test('lumon_browser hard-fails repeated frame_missing results to break model-side retry loops', async (t) => {
  const originalFetch = global.fetch;
  let attempts = 0;

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      attempts += 1;
      return {
        ok: true,
        json: async () => ({
          command_id: `cmd_${attempts}`,
          command: 'open',
          status: 'partial',
          summary_text: 'Opened wikipedia.org, but no fresh frame was captured.',
          reason: 'frame_missing',
          open_url: 'http://127.0.0.1:5173/?session_id=lumon_tool',
          evidence: { frame_emitted: false },
          meta: {},
        }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const first = JSON.parse(
    await plugin.tool.lumon_browser.execute(
      { command_id: 'cmd_a', command: 'open', url: 'https://www.wikipedia.org' },
      { sessionID: 'sess_retry', directory: '/repo', metadata: () => {} },
    ),
  );
  const second = JSON.parse(
    await plugin.tool.lumon_browser.execute(
      { command_id: 'cmd_b', command: 'open', url: 'https://www.wikipedia.org' },
      { sessionID: 'sess_retry', directory: '/repo', metadata: () => {} },
    ),
  );

  assert.equal(first.status, 'partial');
  assert.equal(second.status, 'failed');
  assert.equal(second.reason, 'repeated_frame_missing');
});

test('lumon_browser resets frame-missing retry state on a new begin_task', async (t) => {
  const originalFetch = global.fetch;
  const responses = [
    {
      command_id: 'cmd_open_1',
      command: 'open',
      status: 'partial',
      reason: 'frame_missing',
      summary_text: 'Opened the page, but Lumon did not capture a fresh visible frame yet.',
      evidence: null,
    },
    {
      command_id: 'cmd_begin_2',
      command: 'begin_task',
      status: 'partial',
      reason: 'awaiting_first_navigation',
      summary_text: 'Delegate prepared and waiting for navigation.',
      evidence: null,
    },
    {
      command_id: 'cmd_open_2',
      command: 'open',
      status: 'partial',
      reason: 'frame_missing',
      summary_text: 'Opened the page, but Lumon did not capture a fresh visible frame yet.',
      evidence: null,
    },
  ];

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      return {
        ok: true,
        json: async () => responses.shift(),
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const shellHelper = async () => {};
  const plugin = await createLumonPlugin({
    $: shellHelper,
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  const first = JSON.parse(
    await plugin.tool.lumon_browser.execute(
      { command_id: 'cmd_open_1', command: 'open', url: 'https://www.wikipedia.org' },
      { sessionID: 'sess_retry_reset', directory: '/repo', metadata: () => {} },
    ),
  );
  assert.equal(first.status, 'partial');
  assert.equal(first.reason, 'frame_missing');

  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_begin_2', command: 'begin_task', task_text: 'Open Wikipedia and retry cleanly' },
    { sessionID: 'sess_retry_reset', directory: '/repo', metadata: () => {} },
  );

  const second = JSON.parse(
    await plugin.tool.lumon_browser.execute(
      { command_id: 'cmd_open_2', command: 'open', url: 'https://www.wikipedia.org' },
      { sessionID: 'sess_retry_reset', directory: '/repo', metadata: () => {} },
    ),
  );

  assert.equal(second.status, 'partial');
  assert.equal(second.reason, 'frame_missing');
});

test('lumon_browser uses distinct fallback session ids when tool context omits them', async (t) => {
  const originalFetch = global.fetch;
  const requestBodies = [];

  global.fetch = async (url, options) => {
    const asText = String(url);
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      requestBodies.push(JSON.parse(options.body));
      return {
        ok: true,
        json: async () => ({
          command_id: requestBodies.at(-1).command_id,
          command: requestBodies.at(-1).command,
          status: 'success',
          summary_text: 'Status fetched.',
        }),
      };
    }
    if (asText.endsWith('/healthz')) {
      return okHealthResponse();
    }
    if (asText === 'http://127.0.0.1:5173') {
      return okFrontendResponse();
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_status_1', command: 'status' },
    { directory: '/repo', metadata: () => {} },
  );
  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_status_2', command: 'status' },
    { directory: '/repo', metadata: () => {} },
  );

  assert.equal(requestBodies.length, 2);
  assert.equal(typeof requestBodies[0].observed_session_id, 'string');
  assert.equal(typeof requestBodies[1].observed_session_id, 'string');
  assert.notEqual(requestBodies[0].observed_session_id, requestBodies[1].observed_session_id);
});

test('controller does not prime browser auto-open from partial begin_task results without frame evidence', async () => {
  const originalDateNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  const opens = [];
  const commandCalls = [];
  const controller = createLumonController({
    config: {
      webMode: 'observe_only',
      autoDelegate: false,
      openPolicy: 'browser_or_intervention',
      disableAutoStart: false,
      forceDelegateOnBrowserSignal: true,
      browserEpisodeGapMs: 25000,
      interventionEpisodeGapMs: 10000,
      reopenCooldownMs: 20000,
    },
    attach: async () => ({
      session_id: 'lumon_partial',
      open_url: 'http://127.0.0.1:5173/?session_id=lumon_partial',
      already_attached: false,
    }),
    command: async (payload) => {
      commandCalls.push(payload.command);
      return {
        command_id: payload.command_id,
        command: payload.command,
        status: 'partial',
        summary_text: 'Prepared the delegate, but no frame yet.',
        reason: 'frame_missing',
        evidence: { frame_emitted: false },
      };
    },
    startApp: async () => {},
    waitForHealth: async () => {},
    openUrl: async (url) => opens.push(url),
    log: async () => {},
  });

  await controller.handleEvent({ type: 'session.created', session: { id: 'sess_partial' } }, '/repo');
  await controller.handleEvent({ type: 'tool.execute.after', session: { id: 'sess_partial' }, tool: { name: 'webfetch', url: 'https://example.com' } }, '/repo');
  now += 6000;
  await controller.handleEvent({ type: 'tool.execute.after', session: { id: 'sess_partial' }, tool: { name: 'webfetch', url: 'https://example.com/docs' } }, '/repo');

  assert.deepEqual(commandCalls, ['begin_task']);
  assert.deepEqual(opens, []);
  Date.now = originalDateNow;
});

test('lumon_browser fails fast when the backend runtime version is stale', async (t) => {
  const originalFetch = global.fetch;

  global.fetch = async (url) => {
    const asText = String(url);
    if (asText.endsWith('/healthz')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          protocol_version: '1.3.1',
          runtime_version: 'old-runtime',
        }),
      };
    }
    throw new Error(`unexpected fetch ${asText}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await assert.rejects(
    () =>
      plugin.tool.lumon_browser.execute(
        { command_id: 'cmd_stale', command: 'status' },
        { sessionID: 'sess_123', directory: '/repo', metadata: () => {} },
      ),
    /Stale Lumon backend detected/,
  );
});

test('lumon_browser falls back to plugin directory when context.directory is missing', async (t) => {
  const originalFetch = global.fetch;
  let commandRequestBody = null;

  global.fetch = async (url, options) => {
    const asText = String(url);
    if (asText.endsWith('/healthz')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          protocol_version: '1.3.1',
          runtime_version: '2026-03-16-browser-flow-v3',
        }),
      };
    }
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      commandRequestBody = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          command_id: 'cmd_status',
          command: 'status',
          status: 'success',
          summary_text: 'Status fetched.',
        }),
      };
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo-from-plugin',
    worktree: '/repo-from-plugin',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_status', command: 'status' },
    { sessionID: 'sess_123', metadata: () => {} },
  );

  assert.equal(commandRequestBody.project_directory, '/repo-from-plugin');
});

test('lumon_browser falls back to generated session id when context session keys are missing', async (t) => {
  const originalFetch = global.fetch;
  let commandRequestBody = null;

  global.fetch = async (url, options) => {
    const asText = String(url);
    if (asText.endsWith('/healthz')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          protocol_version: '1.3.1',
          runtime_version: '2026-03-16-browser-flow-v3',
        }),
      };
    }
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      commandRequestBody = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          command_id: commandRequestBody.command_id,
          command: 'status',
          status: 'success',
          summary_text: 'Status fetched.',
        }),
      };
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.tool.lumon_browser.execute(
    { command_id: 'cmd_status', command: 'status' },
    { directory: '/repo', metadata: () => {} },
  );

  assert.equal(typeof commandRequestBody.observed_session_id, 'string');
  assert.equal(commandRequestBody.observed_session_id.length > 0, true);
  assert.notEqual(commandRequestBody.observed_session_id, 'undefined');
});

test('lumon_browser generates command_id when tool args omit command_id', async (t) => {
  const originalFetch = global.fetch;
  let commandRequestBody = null;

  global.fetch = async (url, options) => {
    const asText = String(url);
    if (asText.endsWith('/healthz')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          protocol_version: '1.3.1',
          runtime_version: '2026-03-16-browser-flow-v3',
        }),
      };
    }
    if (asText.endsWith('/api/local/opencode/browser/command')) {
      commandRequestBody = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          command_id: commandRequestBody.command_id,
          command: commandRequestBody.command,
          status: 'success',
          summary_text: 'Status fetched.',
        }),
      };
    }
    throw new Error(`unexpected fetch ${asText}`);
  };

  t.after(() => {
    global.fetch = originalFetch;
  });

  const plugin = await createLumonPlugin({
    $: async () => {},
    directory: '/repo',
    worktree: '/repo',
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_1' },
    client: { app: { log: async () => {} } },
  });

  await plugin.tool.lumon_browser.execute({ command: 'status' }, { sessionID: 'sess_123', directory: '/repo', metadata: () => {} });

  assert.equal(typeof commandRequestBody.command_id, 'string');
  assert.equal(commandRequestBody.command_id.length > 0, true);
  assert.match(commandRequestBody.command_id, /^cmd_status_/);
});
