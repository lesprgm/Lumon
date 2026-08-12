import { createLumonBrowserTool } from "./lumonBrowserTool.js";
import { createLumonController } from "./lumonController.js";
import {
  ACTIVE_BROWSER_TASK_WINDOW_MS,
  attachWithAutoStart,
  browserCommandWithAutoStart,
  buildAttachPayload,
  classifyOpenSignal,
  collectMessageText,
  debugTrace,
  eventTypeOf,
  extractProjectDirectory,
  extractSessionId,
  isAttachRelevantEvent,
  isTerminalEvent,
  loadPluginConfig,
  looksInteractiveBrowserPrompt,
  resolveSessionIdForPromptSteering,
  shouldOpenForEvent,
} from "./lumonPluginShared.js";
import { createRuntimeHelpers } from "./lumonRuntime.js";

export {
  attachWithAutoStart,
  browserCommandWithAutoStart,
  buildAttachPayload,
  classifyOpenSignal,
  createLumonController,
  eventTypeOf,
  extractProjectDirectory,
  extractSessionId,
  isAttachRelevantEvent,
  isTerminalEvent,
  loadPluginConfig,
  shouldOpenForEvent,
};

export function createLumonPlugin(input) {
  const { $, directory, client } = input;
  const config = loadPluginConfig();
  const helpers = createRuntimeHelpers({ $, directory, client, config });
  const commandActivity = new Map();
  const pendingPromptSteering = new Map();
  const activeBrowserTasks = new Map();
  const controller = createLumonController({ config, ...helpers, commandActivity, pendingPromptSteering, activeBrowserTasks });

  return (async () => {
    debugTrace("plugin.init", { directory, webMode: config.webMode });
    await helpers.log(`plugin ready (${config.webMode})`);
    const plugin = {
      event: async ({ event }) => {
        await controller.handleEvent(event, directory);
      },
      tool: {
        lumon_browser: createLumonBrowserTool({
          config,
          helpers: {
            ...helpers,
            sessions: controller.sessions,
            upsertSessionFromTool: controller.upsertSessionFromTool,
            markSessionStopped: controller.markSessionStopped,
            stopAutoResumePollingIfIdle: controller.stopAutoResumePollingIfIdle,
            commandActivity,
            pendingPromptSteering,
            activeBrowserTasks,
          },
        }),
      },
      "tool.definition": async (input, output) => {
        if (input.toolID !== "lumon_browser") {
          return;
        }
        output.description =
          "Use Lumon for interactive browser work. This tool is required when the user asks to open a page, inspect actionable elements, click, type, scroll, wait for page state, or stop before submitting. Never narrate browser success without this tool returning verified evidence. If the tool returns partial with reason=frame_missing, call status once at most and then report the delegate problem instead of looping open or inspect.";
      },
    };
    if (config.enablePromptSteering) {
      plugin["chat.message"] = async (_input, output) => {
        const promptText = collectMessageText(output.parts);
        if (!looksInteractiveBrowserPrompt(promptText)) {
          return;
        }
        const sessionId = resolveSessionIdForPromptSteering(_input, output);
        if (sessionId) {
          pendingPromptSteering.set(sessionId, Date.now() + 15000);
          activeBrowserTasks.set(sessionId, {
            source: "prompt_steering",
            expiresAt: Date.now() + ACTIVE_BROWSER_TASK_WINDOW_MS,
          });
          debugTrace("chat.message.pending_tool", { sessionId, promptText });
        } else {
          debugTrace("chat.message.pending_tool_missing_session", { promptText });
        }
        output.message.tools = {
          ...(output.message.tools || {}),
          lumon_browser: true,
          webfetch: false,
        };
      };
      plugin["experimental.chat.system.transform"] = async (_input, output) => {
        output.system.push(
          "For interactive browser tasks, use the `lumon_browser` tool. Use read-only web tools only for fetching or summarizing content. Never claim a browser click, type, navigation, or scroll succeeded unless `lumon_browser` returned verified success evidence. If `lumon_browser` returns partial with reason=frame_missing, use `status` once at most and then stop with a clear delegate failure instead of looping open or inspect.",
        );
      };
    }
    return plugin;
  })();
}
