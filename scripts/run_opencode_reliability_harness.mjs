#!/usr/bin/env node
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';

const WebSocket = await (async () => {
  if (typeof globalThis.WebSocket === 'function') {
    return globalThis.WebSocket;
  }
  const wsModule = await import('ws');
  return wsModule.WebSocket;
})();

import { createLumonPlugin } from '../.opencode/lib/lumonPluginCore.js';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUTPUT_DIR = path.join(ROOT, 'output', 'manual_checks');

function loadRuntimeOrigins() {
  const backendEnvPath = path.join(ROOT, 'output', 'runtime', 'lumon_backend.env');
  let backendOrigin = 'http://127.0.0.1:8000';
  let frontendOrigin = backendOrigin;
  if (existsSync(backendEnvPath)) {
    const lines = readFileSync(backendEnvPath, 'utf8').split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const [key, ...rest] = trimmed.split('=');
      const value = rest.join('=').trim();
      if (key === 'VITE_LUMON_BACKEND_ORIGIN') {
        backendOrigin = value || backendOrigin;
        continue;
      }
      if (key === 'VITE_LUMON_FRONTEND_ORIGIN' || key === 'LUMON_FRONTEND_ORIGIN') {
        frontendOrigin = value || frontendOrigin;
      }
    }
  }
  const backendUrl = backendOrigin.replace(/\/$/, '');
  const frontendUrl = (frontendOrigin || backendOrigin).replace(/\/$/, '');
  const parsed = new URL(backendUrl);
  const wsBaseUrl = `${parsed.protocol === 'https:' ? 'wss' : 'ws'}://${parsed.host}`;
  return {
    BACKEND_URL: backendUrl,
    FRONTEND_URL: frontendUrl,
    WS_BASE_URL: wsBaseUrl,
  };
}

let { BACKEND_URL, FRONTEND_URL, WS_BASE_URL } = loadRuntimeOrigins();
const LOG_PATHS = {
  plugin: '/tmp/lumon-plugin-debug.log',
  backend: '/tmp/lumon-backend.log',
  frontend: '/tmp/lumon-frontend.log',
};
const DEFAULT_EXTERNAL_PROMPT = 'Open https://www.wikipedia.org, click the search box, type "OpenAI", and stop before submitting.';
const DEFAULT_LOCAL_PROMPT = 'Open the local trace page, click the search box, type OpenAI, and stop before submitting.';
const LOCAL_TRACE_URL = `${BACKEND_URL}/__lumon_harness__/search`;
const LOCAL_APPROVAL_URL = `${BACKEND_URL}/__lumon_harness__/approval`;

function parseArgs(argv) {
  const args = {
    scenario: 'all',
    restart: false,
    outputDir: null,
    tailLines: 160,
    externalPrompt: DEFAULT_EXTERNAL_PROMPT,
    enableWs: false,
    requireDirectTakeover: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === '--restart') {
      args.restart = true;
      continue;
    }
    if (token === '--scenario') {
      args.scenario = argv[index + 1] || args.scenario;
      index += 1;
      continue;
    }
    if (token === '--output-dir') {
      args.outputDir = argv[index + 1] || null;
      index += 1;
      continue;
    }
    if (token === '--tail-lines') {
      args.tailLines = Number(argv[index + 1] || args.tailLines);
      index += 1;
      continue;
    }
    if (token === '--external-prompt') {
      args.externalPrompt = argv[index + 1] || args.externalPrompt;
      index += 1;
      continue;
    }
    if (token === '--enable-ws') {
      args.enableWs = true;
      continue;
    }
    if (token === '--require-direct-takeover') {
      args.requireDirectTakeover = true;
      continue;
    }
  }
  return args;
}

function stamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    '_',
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join('');
}

async function waitForHttp(url, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // wait and retry
    }
    await sleep(250);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function npmExecutable() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm';
}

function ensureHarnessDependencies() {
  try {
    execFileSync(
      process.execPath,
      ['-e', 'import("ws").then(()=>process.exit(0)).catch(()=>process.exit(1))'],
      { cwd: ROOT, stdio: 'ignore' },
    );
    return;
  } catch {
    execFileSync(npmExecutable(), ['install', '--no-audit', '--no-fund', '--save-dev', 'ws@8'], {
      cwd: ROOT,
      stdio: 'inherit',
    });
  }
}

function restartServices() {
  execFileSync('./lumon', ['restart'], {
    cwd: ROOT,
    stdio: 'inherit',
  });
  ({ BACKEND_URL, FRONTEND_URL, WS_BASE_URL } = loadRuntimeOrigins());
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseOpenUrl(openUrl) {
  const parsed = new URL(openUrl);
  return {
    sessionId: parsed.searchParams.get('session_id'),
    token: parsed.searchParams.get('ws_token'),
    wsPath: decodeURIComponent(parsed.searchParams.get('ws_path') || '/ws/session'),
    frontendUrl: `${parsed.origin}${parsed.pathname}${parsed.search}`,
  };
}

function makeHarnessSessionId(label) {
  return `sess_harness_${label}_${stamp()}_${Math.random().toString(36).slice(2, 8)}`;
}

async function tailFile(filePath, lineCount) {
  if (!existsSync(filePath)) {
    return `(missing) ${filePath}`;
  }
  const text = await readFile(filePath, 'utf8');
  const lines = text.split(/\r?\n/).filter((line, index, all) => !(index === all.length - 1 && line === ''));
  return lines.slice(-lineCount).join('\n') || '(empty)';
}

function choosePrimaryTarget(actionableElements) {
  if (!Array.isArray(actionableElements) || actionableElements.length === 0) {
    return null;
  }
  const preferredLabels = ['search', 'search wikipedia', 'search box', 'search input'];
  const typeable = actionableElements.filter((item) => item?.typeable);
  for (const item of typeable) {
    const label = String(item?.label || '').toLowerCase();
    if (preferredLabels.some((token) => label.includes(token))) {
      return item;
    }
  }
  if (typeable.length > 0) {
    return typeable[0];
  }
  const clickable = actionableElements.filter((item) => item?.clickable);
  return clickable[0] || actionableElements[0];
}

class WebSocketCollector {
  constructor(sessionInfo) {
    this.sessionInfo = sessionInfo;
    this.messages = [];
    this.closeEvent = null;
    this.socket = null;
    this.ready = false;
    this._closedPromise = null;
    this._closedResolve = null;
  }

  connect() {
    if (!this.sessionInfo?.sessionId || !this.sessionInfo?.token) {
      throw new Error('Cannot connect websocket without session/token');
    }
    const wsUrl = `${WS_BASE_URL}${this.sessionInfo.wsPath}?session_id=${encodeURIComponent(this.sessionInfo.sessionId)}&token=${encodeURIComponent(this.sessionInfo.token)}`;
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(wsUrl, {
        origin: FRONTEND_URL,
      });
      this.socket = socket;
      this._closedPromise = new Promise((closedResolve) => {
        this._closedResolve = closedResolve;
      });
      let settled = false;
      socket.onopen = () => {
        this.ready = true;
        this.messages.push({ at: Date.now(), type: 'socket_open' });
        socket.send(JSON.stringify({ type: 'ui_ready', payload: { ready: true } }));
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      socket.onmessage = (event) => {
        try {
          this.messages.push({ at: Date.now(), payload: JSON.parse(String(event.data)) });
        } catch (error) {
          this.messages.push({ at: Date.now(), type: 'invalid_json', error: String(error) });
        }
      };
      socket.onclose = (event) => {
        this.closeEvent = { code: event.code, reason: event.reason };
        this.messages.push({ at: Date.now(), type: 'socket_close', code: event.code, reason: event.reason });
        if (typeof this._closedResolve === 'function') {
          this._closedResolve();
          this._closedResolve = null;
        }
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      socket.onerror = (event) => {
        this.messages.push({ at: Date.now(), type: 'socket_error', detail: String(event?.message || 'socket error') });
        if (!settled) {
          settled = true;
          reject(new Error('WebSocket connection failed'));
        }
      };
    });
  }

  send(message) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  async settle(idleMs = 800) {
    await sleep(idleMs);
  }

  async close() {
    if (!this.socket) {
      return;
    }
    if (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING) {
      this.socket.close();
      if (this._closedPromise) {
        await Promise.race([this._closedPromise, sleep(1200)]);
      } else {
        await sleep(150);
      }
    }
    this.socket = null;
  }
}

function buildShellRecorder({ allowShellStarts = false }) {
  const records = [];
  const shell = async (strings, ...values) => {
    const command = String.raw({ raw: strings }, ...values);
    const record = { at: Date.now(), command };
    const openMatch = command.match(/(?:^|\s)(open|xdg-open)\s+(["'])(.*?)\2/);
    if (openMatch) {
      record.kind = 'open';
      record.url = openMatch[3];
    } else if (command.includes('start_demo_backend.sh')) {
      record.kind = 'start_backend';
    } else if (command.includes('start_demo_frontend.sh')) {
      record.kind = 'start_frontend';
    } else {
      record.kind = 'shell';
    }
    records.push(record);
    if (allowShellStarts && (record.kind === 'start_backend' || record.kind === 'start_frontend')) {
      execFileSync('/bin/zsh', ['-lc', command], { cwd: ROOT, stdio: 'ignore' });
    }
  };
  return { shell, records };
}

async function createPluginHarness() {
  const clientLogs = [];
  const promptAsyncCalls = [];
  const shellRecorder = buildShellRecorder({ allowShellStarts: false });
  const plugin = await createLumonPlugin({
    $: shellRecorder.shell,
    directory: ROOT,
    worktree: ROOT,
    serverUrl: new URL('http://127.0.0.1:59207'),
    project: { id: 'proj_reliability' },
    client: {
      app: { log: async (message) => clientLogs.push({ at: Date.now(), message }) },
      session: {
        promptAsync: async (payload) => {
          promptAsyncCalls.push({ at: Date.now(), payload });
        },
      },
    },
  });
  return {
    plugin,
    shellRecords: shellRecorder.records,
    clientLogs,
    promptAsyncCalls,
  };
}

async function executeTool(plugin, args, context) {
  const startedAt = Date.now();
  const raw = await plugin.tool.lumon_browser.execute(args, context);
  const endedAt = Date.now();
  return {
    command: args.command,
    command_id: args.command_id,
    startedAt,
    endedAt,
    durationMs: endedAt - startedAt,
    result: JSON.parse(raw),
  };
}

async function runTask({
  plugin,
  shellRecords,
  sessionId,
  prompt,
  openUrl,
  scenario,
  emitObserverBeforeTool = true,
  collectorRef = null,
  enableWs = false,
  autoApproveBlocked = true,
  stopAfterClick = false,
}) {
  const shellStartIndex = shellRecords.length;
  const promptOutput = {
    message: { id: `msg_${scenario}`, sessionID: sessionId, tools: { webfetch: true } },
    parts: [
      {
        id: `part_${scenario}`,
        sessionID: sessionId,
        messageID: `msg_${scenario}`,
        type: 'text',
        text: prompt,
      },
    ],
  };

  if (typeof plugin['chat.message'] === 'function') {
    await plugin['chat.message']({ sessionID: sessionId }, promptOutput);
  }

  await plugin.event({ event: { type: 'session.created', session: { id: sessionId } } });
  if (emitObserverBeforeTool) {
    await plugin.event({
      event: {
        type: 'message.part.updated',
        session: { id: sessionId },
        content: 'Browser showed new visible output.',
      },
    });
  }

  const commands = [];
  const context = { sessionID: sessionId, directory: ROOT, metadata: () => {} };
  const begin = await executeTool(plugin, {
    command_id: `cmd_begin_${scenario}`,
    command: 'begin_task',
    task_text: prompt,
  }, context);
  commands.push(begin);

  let sessionInfo = null;
  let collector = enableWs ? collectorRef?.collector || null : null;

  const refreshSessionInfoFromShell = () => {
    if (sessionInfo) {
      return;
    }
    const shellOpen = [...shellRecords.slice(shellStartIndex)]
      .reverse()
      .find((record) => record.kind === 'open' && typeof record.url === 'string' && record.url.includes('session_id='));
    if (shellOpen) {
      sessionInfo = parseOpenUrl(shellOpen.url);
    }
  };

  const ensureCollector = async () => {
    if (!enableWs || collector || !collectorRef) {
      return;
    }
    refreshSessionInfoFromShell();
    if (!sessionInfo?.sessionId || !sessionInfo?.token) {
      return;
    }
    collector = new WebSocketCollector(sessionInfo);
    await collector.connect();
    collectorRef.collector = collector;
  };

  const finalizeTask = async () => {
    if (enableWs && collector) {
      await collector.settle(1200);
    }
    refreshSessionInfoFromShell();
    let artifact = null;
    if (sessionInfo?.sessionId) {
      const response = await fetch(`${BACKEND_URL}/api/session-artifacts/${sessionInfo.sessionId}`);
      if (response.ok) {
        artifact = await response.json();
      }
    }
    return {
      scenario,
      sessionId,
      prompt,
      promptSteered: promptOutput.message.tools?.lumon_browser === true && promptOutput.message.tools?.webfetch === false,
      promptTools: promptOutput.message.tools,
      sessionInfo,
      commands,
      artifact,
      shellStartIndex,
      shellEndIndex: shellRecords.length,
    };
  };

  if (begin.result?.open_url) {
    sessionInfo = parseOpenUrl(begin.result.open_url);
  }
  await ensureCollector();

  if (openUrl) {
    const open = await executeTool(plugin, {
      command_id: `cmd_open_${scenario}`,
      command: 'open',
      url: openUrl,
    }, context);
    commands.push(open);
    sessionInfo = sessionInfo || (open.result?.open_url ? parseOpenUrl(open.result.open_url) : null);
    await ensureCollector();
  }

  const inspect = await executeTool(plugin, {
    command_id: `cmd_inspect_${scenario}`,
    command: 'inspect',
  }, context);
  commands.push(inspect);
  await ensureCollector();
  const target = choosePrimaryTarget(inspect.result?.actionable_elements || []);

  if (target?.clickable) {
    const click = await executeTool(plugin, {
      command_id: `cmd_click_${scenario}`,
      command: 'click',
      element_id: target.element_id,
    }, context);
    commands.push(click);
    await ensureCollector();
    if (enableWs && click.result?.status === 'blocked' && click.result?.checkpoint_id && collector && autoApproveBlocked) {
      collector.send({ type: 'approve', payload: { checkpoint_id: click.result.checkpoint_id } });
      await collector.settle(800);
      const postApproveStatus = await executeTool(plugin, {
        command_id: `cmd_status_after_approve_${scenario}`,
        command: 'status',
      }, context);
      commands.push(postApproveStatus);
    }
    if (stopAfterClick) {
      return await finalizeTask();
    }
  }

  if (target?.typeable) {
    const type = await executeTool(plugin, {
      command_id: `cmd_type_${scenario}`,
      command: 'type',
      element_id: target.element_id,
      text: 'OpenAI',
    }, context);
    commands.push(type);
  }

  const status = await executeTool(plugin, {
    command_id: `cmd_status_${scenario}`,
    command: 'status',
  }, context);
  commands.push(status);

  const stop = await executeTool(plugin, {
    command_id: `cmd_stop_${scenario}`,
    command: 'stop',
  }, context);
  commands.push(stop);
  return await finalizeTask();
}

function commandKey(record) {
  return `${record.command}:${record.command_id}`;
}

function findBlockedCheckpoint(task) {
  if (!task || !Array.isArray(task.commands)) {
    return null;
  }
  for (const item of task.commands) {
    if (item?.result?.status === 'blocked' && item?.result?.checkpoint_id) {
      return item.result.checkpoint_id;
    }
  }
  return null;
}

async function executeWithBusyRetry(plugin, args, context, retries = 4, delayMs = 400) {
  let lastResult = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const executed = await executeTool(plugin, args, context);
    lastResult = executed;
    const isBusy =
      executed?.result?.status === 'blocked' &&
      executed?.result?.reason === 'busy';
    if (!isBusy || attempt === retries) {
      return executed;
    }
    await sleep(delayMs);
  }
  return lastResult;
}

async function waitForAutoResumePrompt(clientLogs, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const latest = clientLogs.find((entry) => {
      const message = String(entry?.message || '');
      return (
        message.includes('[lumon] auto_resume_prompt') ||
        message.includes('[lumon] auto_resume_prompt_error')
      );
    });
    if (latest) {
      return latest;
    }
    await sleep(250);
  }
  return null;
}

function assertCondition(assertions, condition, code, detail) {
  assertions.push({ code, pass: Boolean(condition), detail });
}

function evaluateTask(task, shellRecords, wsMessages) {
  const assertions = [];
  const taskShellRecords = shellRecords.slice(task.shellStartIndex, task.shellEndIndex);
  const shellOpens = taskShellRecords.filter((record) => record.kind === 'open');
  const eligible = task.commands.filter(({ result }) => {
    if (!result || typeof result !== 'object') {
      return false;
    }
    if (result.intervention_id || result.status === 'blocked') {
      return true;
    }
    if (result.command === 'begin_task') {
      return false;
    }
    return Boolean(result.evidence?.frame_emitted);
  });
  const firstEligibleAt = eligible.length > 0 ? eligible[0].startedAt : null;
  const firstOpenAt = shellOpens.length > 0 ? shellOpens[0].at : null;
  assertCondition(assertions, task.promptSteered, 'prompt_steered', task.promptTools);
  assertCondition(
    assertions,
    firstOpenAt === null || firstEligibleAt === null || firstOpenAt >= firstEligibleAt,
    'no_premature_ui_open',
    { firstOpenAt, firstEligibleAt },
  );
  assertCondition(assertions, shellOpens.length <= 1, 'single_ui_open_per_task', { openCount: shellOpens.length });

  let sawVerifiedFrame = false;
  let consecutiveFrameMissing = 0;
  for (const item of task.commands) {
    const result = item.result || {};
    const frameEmitted = Boolean(result?.evidence?.frame_emitted);
    const verified = Boolean(result?.evidence?.verified);
    if (frameEmitted || verified) {
      sawVerifiedFrame = true;
    }
    if (result.status === 'partial' && result.reason === 'frame_missing') {
      consecutiveFrameMissing += 1;
    } else {
      consecutiveFrameMissing = 0;
    }
    if (result.status === 'success' && !['begin_task', 'stop'].includes(item.command)) {
      assertCondition(
        assertions,
        verified || frameEmitted || Boolean(result.intervention_id),
        `success_has_evidence:${commandKey(item)}`,
        result,
      );
    }
    if (sawVerifiedFrame && ['open', 'status'].includes(item.command)) {
      assertCondition(
        assertions,
        !(result.status === 'partial' && result.reason === 'frame_missing'),
        `no_late_frame_missing:${commandKey(item)}`,
        result,
      );
    }
  }
  assertCondition(assertions, consecutiveFrameMissing < 2, 'no_frame_missing_retry_spiral', { commands: task.commands.map((item) => item.result) });
  assertCondition(
    assertions,
    sawVerifiedFrame || wsMessages.some((message) => message?.payload?.type === 'frame' || message?.payload?.payload?.mime_type),
    'visible_frame_arrived',
    { sawVerifiedFrame, wsMessages: wsMessages.length },
  );

  const artifactCommandEntries = Array.isArray(task.artifact?.commands)
    ? task.artifact.commands
    : Array.isArray(task.artifact?.artifact?.browser_commands)
      ? task.artifact.artifact.browser_commands
      : [];
  const artifactCommandMap = new Map(
    artifactCommandEntries.map((command) => [`${command.command}:${command.command_id}`, command]),
  );
  const taskCommands = task.commands.map((item) => `${item.command}:${item.command_id}`);
  assertCondition(
    assertions,
    task.commands.every((item) => {
      const artifactCommand = artifactCommandMap.get(`${item.command}:${item.command_id}`);
      if (!artifactCommand) {
        return false;
      }
      return artifactCommand.status === item.result?.status && (artifactCommand.reason || null) === (item.result?.reason || null);
    }),
    'artifact_matches_command_history',
    { taskCommands, artifactCommands: [...artifactCommandMap.keys()] },
  );

  return assertions;
}

async function runStaleTabScenario(sessionInfo) {
  if (!sessionInfo?.sessionId) {
    return { skipped: true, reason: 'missing_session' };
  }
  const badToken = `${sessionInfo.token || 'ws_invalid'}_stale`;
  const wsUrl = `ws://127.0.0.1:8000${sessionInfo.wsPath}?session_id=${encodeURIComponent(sessionInfo.sessionId)}&token=${encodeURIComponent(badToken)}`;
  return await new Promise((resolve) => {
    const socket = new WebSocket(wsUrl);
    socket.onclose = (event) => {
      resolve({ skipped: false, code: event.code, reason: event.reason });
    };
    socket.onerror = () => {
      resolve({ skipped: false, code: null, reason: 'socket_error' });
    };
  });
}

async function runTakeoverAutoResumeScenario({
  plugin,
  shellRecords,
  clientLogs,
  promptAsyncCalls,
  sessionId,
  collectorRef,
  enableWs,
  requireDirectTakeover = false,
}) {
  const prompt =
    'Open the local approval page, click submit, then wait for manual verification before continuing.';
  const task = await runTask({
    plugin,
    shellRecords,
    sessionId,
    prompt,
    openUrl: LOCAL_APPROVAL_URL,
    scenario: 'takeover_auto_resume',
    enableWs,
    collectorRef,
    autoApproveBlocked: false,
    stopAfterClick: true,
  });

  const checkpointId = findBlockedCheckpoint(task);

  if (!enableWs || !collectorRef?.collector || !checkpointId) {
    return {
      skipped: true,
      reason: !enableWs ? 'websocket_disabled' : !collectorRef?.collector ? 'session_info_missing' : 'checkpoint_missing',
      task,
      autoResumeLog: null,
      promptAsyncCount: Array.isArray(promptAsyncCalls) ? promptAsyncCalls.length : 0,
    };
  }

  const collector = collectorRef.collector;
  collector.send({
    type: 'start_takeover',
    payload: {
      mode_preference: requireDirectTakeover ? 'direct' : 'remote',
    },
  });
  await collector.settle(500);
  const takeoverStatus = await executeWithBusyRetry(
    plugin,
    {
      command_id: `cmd_status_takeover_mode_${sessionId}`,
      command: 'status',
    },
    { sessionID: sessionId, directory: ROOT, metadata: () => {} },
    6,
    450,
  );
  collector.send({ type: 'end_takeover', payload: {} });
  await collector.settle(1200);
  collector.send({ type: 'approve', payload: { checkpoint_id: checkpointId } });
  await collector.settle(800);

  const autoResumeLog = await waitForAutoResumePrompt(clientLogs, 12000);
  const postResumeStatus = await executeWithBusyRetry(
    plugin,
    {
      command_id: `cmd_status_takeover_${sessionId}`,
      command: 'status',
    },
    { sessionID: sessionId, directory: ROOT, metadata: () => {} },
    6,
    450,
  );

  return {
    skipped: false,
    task,
    autoResumeLog,
    takeoverStatus,
    wsMessages: collector.messages,
    postResumeStatus,
    promptAsyncCount: Array.isArray(promptAsyncCalls) ? promptAsyncCalls.length : 0,
    promptAsyncCalls: Array.isArray(promptAsyncCalls) ? promptAsyncCalls : [],
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  ensureHarnessDependencies();
  if (args.restart) {
    restartServices();
  }
  await waitForHttp(`${BACKEND_URL}/healthz`);
  await waitForHttp(FRONTEND_URL);

  const outputDir = args.outputDir || path.join(OUTPUT_DIR, `opencode_reliability_${stamp()}`);
  await mkdir(outputDir, { recursive: true });

  const { plugin, shellRecords, clientLogs, promptAsyncCalls } = await createPluginHarness();
  const results = [];
  const collectorRef = { collector: null };
  const selectedScenarios = new Set(
    args.scenario === 'all'
      ? ['external', 'local', 'approval', 'takeover-auto-resume', 'second-task', 'reconnect', 'stale-tab']
      : args.scenario.split(',').map((value) => value.trim()).filter(Boolean),
  );

  let externalTask = null;
  const sessionIds = {
    external: makeHarnessSessionId('external'),
    local: makeHarnessSessionId('local'),
    approval: makeHarnessSessionId('approval'),
    reuse: makeHarnessSessionId('reuse'),
  };
  if (selectedScenarios.has('external') || selectedScenarios.has('reconnect') || selectedScenarios.has('stale-tab')) {
    externalTask = await runTask({
      plugin,
      shellRecords,
      sessionId: sessionIds.external,
      prompt: args.externalPrompt,
      openUrl: 'https://www.wikipedia.org',
      scenario: 'external',
      emitObserverBeforeTool: true,
      collectorRef,
      enableWs: args.enableWs,
    });
    results.push(externalTask);
  }

  if (selectedScenarios.has('local')) {
    const localTask = await runTask({
      plugin,
      shellRecords,
      sessionId: sessionIds.local,
      prompt: DEFAULT_LOCAL_PROMPT,
      openUrl: LOCAL_TRACE_URL,
      scenario: 'local',
      enableWs: args.enableWs,
    });
    results.push(localTask);
  }

  if (selectedScenarios.has('approval')) {
    const approvalTask = await runTask({
      plugin,
      shellRecords,
      sessionId: sessionIds.approval,
      prompt: 'Open the local approval page, click submit, approve the intervention, and stop.',
      openUrl: LOCAL_APPROVAL_URL,
      scenario: 'approval',
      enableWs: args.enableWs,
    });
    results.push(approvalTask);
  }

  let takeoverAutoResume = { skipped: true, reason: 'scenario_disabled' };
  if (selectedScenarios.has('takeover-auto-resume')) {
    takeoverAutoResume = await runTakeoverAutoResumeScenario({
      plugin,
      shellRecords,
      clientLogs,
      promptAsyncCalls,
      sessionId: makeHarnessSessionId('takeover_resume'),
      collectorRef: { collector: null },
      enableWs: args.enableWs,
      requireDirectTakeover: args.requireDirectTakeover,
    });
    if (takeoverAutoResume?.task) {
      results.push(takeoverAutoResume.task);
    }
  }

  if (selectedScenarios.has('second-task')) {
    const secondTaskCollector = { collector: null };
    const firstSameSessionTask = await runTask({
      plugin,
      shellRecords,
      sessionId: sessionIds.reuse,
      prompt: DEFAULT_LOCAL_PROMPT,
      openUrl: LOCAL_TRACE_URL,
      scenario: 'same_session_first',
      collectorRef: secondTaskCollector,
      enableWs: args.enableWs,
    });
    if (secondTaskCollector.collector) {
      await secondTaskCollector.collector.close();
      secondTaskCollector.collector = null;
    }
    const secondSameSessionTask = await runTask({
      plugin,
      shellRecords,
      sessionId: sessionIds.reuse,
      prompt: 'Open the local trace page again, inspect it, and stop before submitting.',
      openUrl: LOCAL_TRACE_URL,
      scenario: 'same_session_second',
      enableWs: args.enableWs,
    });
    results.push(firstSameSessionTask, secondSameSessionTask);
  }

  let reconnectReplayCollector = { messages: [] };
  if (args.enableWs && externalTask?.sessionInfo && selectedScenarios.has('reconnect')) {
    const reconnectCollector = new WebSocketCollector(externalTask.sessionInfo);
    await reconnectCollector.connect();
    await reconnectCollector.close();
    reconnectReplayCollector = new WebSocketCollector(externalTask.sessionInfo);
    await reconnectReplayCollector.connect();
    await reconnectReplayCollector.settle(600);
    await reconnectReplayCollector.close();
  }

  const staleTab =
    args.enableWs && externalTask?.sessionInfo && selectedScenarios.has('stale-tab')
      ? await runStaleTabScenario(externalTask.sessionInfo)
      : { skipped: true, reason: 'scenario_disabled' };

  const wsMessages = {
    external: collectorRef.collector?.messages || [],
    reconnectReplay: reconnectReplayCollector.messages,
  };
  if (collectorRef.collector) {
    await collectorRef.collector.close();
  }

  const assertions = [];
  for (const task of results) {
    assertions.push(...evaluateTask(task, shellRecords, task.scenario === 'external' ? wsMessages.external : []));
  }
  if (args.enableWs && selectedScenarios.has('stale-tab')) {
    assertCondition(assertions, staleTab.skipped || staleTab.code === 1008, 'stale_tab_1008', staleTab);
  }
  if (args.enableWs && selectedScenarios.has('reconnect')) {
    assertCondition(
      assertions,
      reconnectReplayCollector.messages.some((message) => message?.payload?.type === 'session_state'),
      'reconnect_replays_session_state',
      { messageCount: reconnectReplayCollector.messages.length },
    );
    assertCondition(
      assertions,
      reconnectReplayCollector.messages.some((message) => {
        const type = message?.payload?.type;
        return type === 'browser_context_update' || type === 'frame' || type === 'browser_command';
      }),
      'reconnect_replays_live_state',
      { messages: reconnectReplayCollector.messages },
    );
  }
  if (args.enableWs && selectedScenarios.has('takeover-auto-resume')) {
    assertCondition(
      assertions,
      !takeoverAutoResume.skipped,
      'takeover_auto_resume_executed',
      takeoverAutoResume,
    );
    const wsMessages = Array.isArray(takeoverAutoResume.wsMessages)
      ? takeoverAutoResume.wsMessages
      : [];
    const sawTakeoverState = wsMessages.some(
      (entry) => entry?.payload?.type === 'session_state' && entry?.payload?.payload?.state === 'takeover',
    );
    const sawRunningState = wsMessages.some(
      (entry) => entry?.payload?.type === 'session_state' && entry?.payload?.payload?.state === 'running',
    );
    assertCondition(assertions, sawTakeoverState, 'takeover_state_seen', { wsMessages });
    assertCondition(assertions, sawRunningState, 'takeover_returned_running_seen', { wsMessages });
    assertCondition(
      assertions,
      String(takeoverAutoResume.autoResumeLog?.message || '').includes('auto_resume_prompt'),
      'auto_resume_prompt_logged',
      takeoverAutoResume.autoResumeLog,
    );
    assertCondition(
      assertions,
      Number(takeoverAutoResume.promptAsyncCount || 0) >= 1,
      'auto_resume_prompt_async_called',
      {
        promptAsyncCount: takeoverAutoResume.promptAsyncCount,
        promptAsyncCalls: takeoverAutoResume.promptAsyncCalls,
      },
    );
    const statusResult = takeoverAutoResume.postResumeStatus?.result || {};
    assertCondition(
      assertions,
      statusResult.command === 'status' && ['success', 'partial'].includes(statusResult.status),
      'post_resume_status_command_runs',
      statusResult,
    );
    const takeoverStatusResult = takeoverAutoResume.takeoverStatus?.result || {};
    const takeoverMode = takeoverStatusResult?.meta?.takeover_mode || null;
    const takeoverUrl = takeoverStatusResult?.meta?.takeover_url || null;
    assertCondition(
      assertions,
      typeof takeoverMode === 'string' && ['remote', 'direct'].includes(takeoverMode),
      'takeover_mode_reported',
      takeoverStatusResult,
    );
    if (args.requireDirectTakeover) {
      assertCondition(
        assertions,
        takeoverMode === 'direct',
        'direct_takeover_mode_enabled',
        takeoverStatusResult,
      );
    }
    if (takeoverMode === 'direct') {
      assertCondition(
        assertions,
        typeof takeoverUrl === 'string' && takeoverUrl.length > 0,
        'direct_takeover_url_reported',
        takeoverStatusResult,
      );
    }
  }

  const bundle = {
    generatedAt: new Date().toISOString(),
    runtime: {
      backend: BACKEND_URL,
      frontend: FRONTEND_URL,
    },
    results,
    shellRecords,
    clientLogs,
    promptAsyncCalls,
    wsMessages,
    takeoverAutoResume,
    staleTab,
    assertions,
  };

  await writeFile(path.join(outputDir, 'harness.json'), `${JSON.stringify(bundle, null, 2)}\n`, 'utf8');
  await writeFile(
    path.join(outputDir, 'summary.md'),
    [
      '# OpenCode Reliability Harness',
      '',
      ...assertions.map((assertion) => `- ${assertion.pass ? '[pass]' : '[fail]'} ${assertion.code}`),
      '',
      '## Tasks',
      ...results.map((task) => `- ${task.scenario}: ${task.commands.map((item) => `${item.command}=${item.result.status}`).join(', ')}`),
    ].join('\n') + '\n',
    'utf8',
  );
  for (const [label, filePath] of Object.entries(LOG_PATHS)) {
    await writeFile(path.join(outputDir, `${label}.log.tail.txt`), await tailFile(filePath, args.tailLines), 'utf8');
  }

  const failingAssertions = assertions.filter((assertion) => !assertion.pass);
  if (failingAssertions.length > 0) {
    console.error(JSON.stringify({ outputDir, failingAssertions }, null, 2));
    process.exit(1);
  }
  console.log(JSON.stringify({ outputDir, assertionCount: assertions.length }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
