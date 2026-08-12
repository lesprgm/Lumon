import {
  DEFAULT_AUTO_RESUME_BUSY_MAX_RETRIES,
  DEFAULT_AUTO_RESUME_BUSY_RETRY_MS,
  DEFAULT_RESUME_INTENT_POLL_MS,
  OPEN_SIGNAL_DEDUPE_WINDOW_MS,
  attachWithAutoStart,
  browserCommandWithAutoStart,
  buildAttachPayload,
  buildOpenSignalKey,
  classifyOpenSignal,
  debugTrace,
  eventTypeOf,
  extractProjectDirectory,
  extractSessionId,
  extractSessionState,
  extractTakeoverMetadata,
  isAttachRelevantEvent,
  isDirectTakeoverActiveContext,
  isTerminalEvent,
  resolveDirectory,
  shouldForegroundLumonOnResume,
  shouldOpenForBrowserCommandResult,
  shouldOpenForEvent,
  sleep,
} from "./lumonPluginShared.js";

export function createLumonController({
  config,
  attach,
  command,
  consumeResumeIntent,
  acknowledgeResumeIntent,
  continueSession,
  startApp,
  waitForHealth,
  openUrl,
  recordUiTelemetry = async () => {},
  log = async () => {},
  commandActivity = new Map(),
  pendingPromptSteering = new Map(),
  activeBrowserTasks = new Map(),
}) {
  const sessions = new Map();
  const inflight = new Map();
  let startupPromise = null;
  let recentDelegateFailures = 0;
  let resumePoller = null;
  let resumePollInFlight = false;

  const fireUiTelemetry = ({ sessionId, event, meta = {} }) => {
    if (typeof recordUiTelemetry === "function") {
      return recordUiTelemetry({ sessionId, event, meta, source: "plugin" });
    }
    return Promise.resolve();
  };

  async function ensureStarted() {
    if (!startupPromise) {
      startupPromise = (async () => {
        await startApp();
        await waitForHealth();
      })().finally(() => {
        startupPromise = null;
      });
    }
    return startupPromise;
  }

  async function ensureAttached(event, directory, { forceRefresh = false } = {}) {
    const observedSessionId = extractSessionId(event);
    debugTrace("ensureAttached", { eventType: eventTypeOf(event), observedSessionId });
    if (!observedSessionId) return null;
    if (!forceRefresh && sessions.has(observedSessionId)) return sessions.get(observedSessionId);
    if (inflight.has(observedSessionId)) return inflight.get(observedSessionId);

    const payload = buildAttachPayload({ event, config, directory });
    const previousSession = sessions.get(observedSessionId) || null;
    let autoStartLatencyMs = null;
    const promise = attachWithAutoStart({
      attach,
      startApp: ensureStarted,
      waitForHealth,
      payload,
      config,
      log,
      onAutoStartComplete: ({ startupLatencyMs }) => {
        autoStartLatencyMs = startupLatencyMs;
      },
    }).then((response) => {
      const session = {
        observedSessionId,
        projectDirectory: payload.project_directory,
        lumonSessionId: response.session_id,
        openUrl: response.open_url,
        alreadyAttached: Boolean(response.already_attached),
        uiConnected: response.ui_connected === true,
        uiReadyAt: response.ui_ready_at || null,
        lastOpenedAt: previousSession?.lastOpenedAt || 0,
        openInProgress: previousSession?.openInProgress || false,
        lastRelevantBrowserAt: previousSession?.lastRelevantBrowserAt || 0,
        lastRelevantInterventionAt: previousSession?.lastRelevantInterventionAt || 0,
        delegatePrimed: previousSession?.delegatePrimed || false,
        lastDelegatePrimeAt: previousSession?.lastDelegatePrimeAt || 0,
        attachedAt: Date.now(),
        lastOpenSignalKey: previousSession?.lastOpenSignalKey || null,
        lastOpenSignalAt: previousSession?.lastOpenSignalAt || 0,
        takeoverActive: previousSession?.takeoverActive || false,
        takeoverMode: previousSession?.takeoverMode || null,
        takeoverUrl: previousSession?.takeoverUrl || null,
        lastResumeIntentSeq: previousSession?.lastResumeIntentSeq || 0,
        pendingResumeIntentSeq: previousSession?.pendingResumeIntentSeq || 0,
        pendingResumeReason: previousSession?.pendingResumeReason || null,
        autoResumeInFlight: false,
        lastAutoResumeAt: previousSession?.lastAutoResumeAt || 0,
        autoResumeFailureCount: previousSession?.autoResumeFailureCount || 0,
        suppressUntilNextTakeover:
          previousSession?.suppressUntilNextTakeover || false,
        stoppedAt: previousSession?.stoppedAt || null,
      };
      sessions.set(observedSessionId, session);
      if (typeof autoStartLatencyMs === "number") {
        void fireUiTelemetry({
          sessionId: response.session_id,
          event: "auto_start_completed",
          meta: { startup_latency_ms: autoStartLatencyMs, phase: "attach" },
        });
      }
      inflight.delete(observedSessionId);
      return session;
    }).catch((error) => {
      inflight.delete(observedSessionId);
      throw error;
    });

    inflight.set(observedSessionId, promise);
    return promise;
  }

  function stopResumePollerIfIdle() {
    if (sessions.size > 0 || !resumePoller) {
      return;
    }
    clearInterval(resumePoller);
    resumePoller = null;
    resumePollInFlight = false;
  }

  function markSessionStopped(observedSessionId) {
    if (typeof observedSessionId !== "string" || observedSessionId.trim().length === 0) {
      return;
    }
    const session = sessions.get(observedSessionId);
    if (!session) {
      return;
    }
    session.takeoverActive = false;
    session.pendingResumeIntentSeq = 0;
    session.pendingResumeReason = null;
    session.suppressUntilNextTakeover = true;
    session.stoppedAt = Date.now();
  }

  async function executeContinueWithBusyRetry(session, resumeIntent) {
    const retries = Math.max(
      0,
      Number(config.autoResumeBusyMaxRetries || DEFAULT_AUTO_RESUME_BUSY_MAX_RETRIES),
    );
    const retryDelayMs = Math.max(
      100,
      Number(config.autoResumeBusyRetryMs || DEFAULT_AUTO_RESUME_BUSY_RETRY_MS),
    );
    let attempt = 0;
    while (attempt <= retries) {
      attempt += 1;
      try {
        await continueSession({
          observedSessionId: session.observedSessionId,
          projectDirectory: session.projectDirectory,
          reason: String(resumeIntent.reason || "takeover_returned_control"),
          takeoverMode: session.takeoverMode || null,
        });
        return { attempts: attempt };
      } catch (error) {
        const message = String(error?.message || error || "").toLowerCase();
        const isBusyError = message.includes("busy") || message.includes("still finishing");
        if (!isBusyError || attempt > retries) {
          throw error;
        }
        await sleep(retryDelayMs);
      }
    }
    return { attempts: retries + 1 };
  }

  function upsertSessionFromTool({ observedSessionId, lumonSessionId, projectDirectory, openUrl = null, uiConnected = false, takeoverMode = null, takeoverUrl = null } = {}) {
    if (typeof observedSessionId !== "string" || observedSessionId.trim().length === 0) {
      return null;
    }
    const previousSession = sessions.get(observedSessionId) || null;
    const resolvedLumonSessionId =
      typeof lumonSessionId === "string" && lumonSessionId.trim().length > 0
        ? lumonSessionId
        : previousSession?.lumonSessionId;
    if (typeof resolvedLumonSessionId !== "string" || resolvedLumonSessionId.trim().length === 0) {
      return previousSession;
    }
    const session = {
      observedSessionId,
      projectDirectory: resolveDirectory(projectDirectory, previousSession?.projectDirectory),
      lumonSessionId: resolvedLumonSessionId,
      openUrl:
        typeof openUrl === "string" && openUrl.trim().length > 0
          ? openUrl
          : previousSession?.openUrl || "",
      alreadyAttached: previousSession?.alreadyAttached || false,
      uiConnected: uiConnected === true || previousSession?.uiConnected === true,
      uiReadyAt: previousSession?.uiReadyAt || null,
      lastOpenedAt: previousSession?.lastOpenedAt || 0,
      openInProgress: previousSession?.openInProgress || false,
      lastRelevantBrowserAt: previousSession?.lastRelevantBrowserAt || 0,
      lastRelevantInterventionAt: previousSession?.lastRelevantInterventionAt || 0,
      delegatePrimed: previousSession?.delegatePrimed || false,
      lastDelegatePrimeAt: previousSession?.lastDelegatePrimeAt || 0,
      attachedAt: previousSession?.attachedAt || Date.now(),
      lastOpenSignalKey: previousSession?.lastOpenSignalKey || null,
      lastOpenSignalAt: previousSession?.lastOpenSignalAt || 0,
      takeoverActive: previousSession?.takeoverActive || false,
      takeoverMode:
        takeoverMode === "remote" || takeoverMode === "direct"
          ? takeoverMode
          : previousSession?.takeoverMode || null,
      takeoverUrl:
        typeof takeoverUrl === "string" && takeoverUrl.trim().length > 0
          ? takeoverUrl
          : previousSession?.takeoverUrl || null,
      lastResumeIntentSeq: previousSession?.lastResumeIntentSeq || 0,
      pendingResumeIntentSeq: previousSession?.pendingResumeIntentSeq || 0,
      pendingResumeReason: previousSession?.pendingResumeReason || null,
      autoResumeInFlight: false,
      lastAutoResumeAt: previousSession?.lastAutoResumeAt || 0,
      autoResumeFailureCount: previousSession?.autoResumeFailureCount || 0,
      suppressUntilNextTakeover:
        previousSession?.suppressUntilNextTakeover || false,
      stoppedAt: previousSession?.stoppedAt || null,
    };
    sessions.set(observedSessionId, session);
    ensureResumePoller();
    return session;
  }

  async function maybeAutoResumeSession(session, source) {
    if (
      !session ||
      typeof consumeResumeIntent !== "function" ||
      typeof continueSession !== "function" ||
      session.autoResumeInFlight
    ) {
      return;
    }
    if (session.suppressUntilNextTakeover === true) {
      return;
    }
    session.autoResumeInFlight = true;
    try {
      const afterSeq = session.pendingResumeIntentSeq
        ? Math.max(0, Number(session.pendingResumeIntentSeq) - 1)
        : session.lastResumeIntentSeq || 0;
      const resumeIntent = await consumeResumeIntent({
        sessionId: session.lumonSessionId,
        afterSeq,
        consume: false,
      });
      if (!resumeIntent || resumeIntent.pending !== true) {
        session.pendingResumeIntentSeq = 0;
        session.pendingResumeReason = null;
        return;
      }
      const resumeSeq = Number(resumeIntent.resume_intent_seq || 0);
      if (resumeSeq <= (session.lastResumeIntentSeq || 0)) {
        session.pendingResumeIntentSeq = 0;
        session.pendingResumeReason = null;
        return;
      }
      session.pendingResumeIntentSeq = resumeSeq;
      session.pendingResumeReason = String(resumeIntent.reason || "takeover_returned_control");
      void fireUiTelemetry({
        sessionId: session.lumonSessionId,
        event: "resume_requested",
        meta: {
          reason_code: String(resumeIntent.reason || "takeover_returned_control"),
          source,
          resume_intent_seq: resumeSeq,
        },
      });
      await log(
        `auto_resume_prompt observed_session_id=${session.observedSessionId} reason=${String(
          resumeIntent.reason || "takeover_returned_control",
        )} resume_intent_seq=${resumeSeq}`,
      );
      const startedAt = Date.now();
      const continueResult = await executeContinueWithBusyRetry(session, resumeIntent);
      if (typeof acknowledgeResumeIntent === "function") {
        await acknowledgeResumeIntent({
          sessionId: session.lumonSessionId,
          resumeIntentSeq: resumeSeq,
        });
      }
      session.lastResumeIntentSeq = resumeSeq;
      session.pendingResumeIntentSeq = 0;
      session.pendingResumeReason = null;
      session.lastAutoResumeAt = Date.now();
      session.autoResumeFailureCount = 0;
      if (
        shouldForegroundLumonOnResume(resumeIntent.reason) &&
        typeof session.openUrl === "string" &&
        session.openUrl.trim().length > 0 &&
        typeof openUrl === "function"
      ) {
        try {
          await openUrl(session.openUrl);
          session.lastOpenedAt = Date.now();
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_completed",
            meta: {
              reason_code: "resume_foreground",
              source,
              resume_intent_seq: resumeSeq,
            },
          });
        } catch (openError) {
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_failed",
            meta: {
              reason_code: "resume_foreground",
              source,
              resume_intent_seq: resumeSeq,
              message: String(openError?.message || openError || ""),
            },
          });
        }
      }
      await log(
        `auto_resume_prompt_succeeded observed_session_id=${session.observedSessionId} resume_intent_seq=${resumeSeq}`,
      );
      void fireUiTelemetry({
        sessionId: session.lumonSessionId,
        event: "resume_succeeded",
        meta: {
          source,
          resume_intent_seq: resumeSeq,
          retry_count: Math.max(0, Number(continueResult?.attempts || 1) - 1),
          latency_ms: Date.now() - startedAt,
        },
      });
    } catch (error) {
      session.autoResumeFailureCount = Number(session.autoResumeFailureCount || 0) + 1;
      if (session.autoResumeFailureCount >= 5) {
        session.suppressUntilNextTakeover = true;
      }
      const errorMessage = String(error?.message || error || "");
      const likelyStaleSession = /\b(404|session not found)\b/i.test(errorMessage);
      if (likelyStaleSession) {
        sessions.delete(session.observedSessionId);
        inflight.delete(session.observedSessionId);
        commandActivity.delete(session.observedSessionId);
        pendingPromptSteering.delete(session.observedSessionId);
        activeBrowserTasks.delete(session.observedSessionId);
        stopResumePollerIfIdle();
      }
      void fireUiTelemetry({
        sessionId: session.lumonSessionId,
        event: "resume_failed",
        meta: {
          source,
          resume_intent_seq: session.pendingResumeIntentSeq || null,
          failure_count: session.autoResumeFailureCount,
          suppressed: session.suppressUntilNextTakeover === true,
          message: errorMessage,
        },
      });
      await log(
        `auto_resume_prompt_error observed_session_id=${session.observedSessionId} message=${String(
          error?.message || error || "unknown error",
        )}`,
      );
      await log(`Lumon auto-resume failed: ${String(error?.message || error || "unknown error")}`);
    } finally {
      session.autoResumeInFlight = false;
    }
  }

  function ensureResumePoller() {
    if (resumePoller || typeof consumeResumeIntent !== "function" || typeof continueSession !== "function") {
      return;
    }
    if (sessions.size === 0) {
      return;
    }
    const intervalMs = Math.max(500, Number(config.resumeIntentPollMs || DEFAULT_RESUME_INTENT_POLL_MS));
    resumePoller = setInterval(async () => {
      if (resumePollInFlight || sessions.size === 0) {
        return;
      }
      resumePollInFlight = true;
      try {
        for (const session of sessions.values()) {
          await maybeAutoResumeSession(session, "poll");
        }
      } finally {
        resumePollInFlight = false;
      }
    }, intervalMs);
    if (typeof resumePoller.unref === "function") {
      resumePoller.unref();
    }
  }

  async function handleEvent(event, directory) {
    const now = Date.now();
    const observedSessionId = extractSessionId(event);
    const relevant = isAttachRelevantEvent(event);
    const signal = classifyOpenSignal(event);
    const activeBrowserTask = observedSessionId ? activeBrowserTasks.get(observedSessionId) : null;
    const browserTaskActive = Boolean(activeBrowserTask && typeof activeBrowserTask.expiresAt === "number" && activeBrowserTask.expiresAt > now);
    if (observedSessionId && activeBrowserTask && !browserTaskActive) {
      activeBrowserTasks.delete(observedSessionId);
    }
    debugTrace("handleEvent", { eventType: eventTypeOf(event), observedSessionId, relevant });
    if (browserTaskActive && signal !== null) {
      debugTrace("openSignal.suppressed_active_browser_task", {
        observedSessionId,
        signal,
        eventType: eventTypeOf(event),
        expiresAt: activeBrowserTask.expiresAt,
      });
      const runtimeSessionId = sessions.get(observedSessionId)?.lumonSessionId;
      if (runtimeSessionId) {
        void fireUiTelemetry({
          sessionId: runtimeSessionId,
          event: "open_suppressed",
          meta: { reason_code: "active_browser_task", signal, event_type: eventTypeOf(event) },
        });
      }
      return;
    }
    if (relevant && observedSessionId && (signal !== null || sessions.has(observedSessionId))) {
      await ensureAttached(event, directory);
      ensureResumePoller();
    }

    if (!observedSessionId) return;
    const session = sessions.get(observedSessionId);
    if (session) {
      const sessionState = extractSessionState(event);
      const takeoverMeta = extractTakeoverMetadata(event);
      if (takeoverMeta.takeoverMode) {
        session.takeoverMode = takeoverMeta.takeoverMode;
      }
      if (takeoverMeta.takeoverUrl) {
        session.takeoverUrl = takeoverMeta.takeoverUrl;
      }
      if (sessionState === "takeover") {
        session.takeoverActive = true;
        session.suppressUntilNextTakeover = false;
        session.autoResumeFailureCount = 0;
        session.stoppedAt = null;
        if (session.pendingResumeIntentSeq > 0) {
          session.pendingResumeIntentSeq = 0;
          session.pendingResumeReason = null;
        }
        ensureResumePoller();
      } else if (session.takeoverActive && sessionState === "running") {
        session.takeoverActive = false;
        await maybeAutoResumeSession(session, "event_transition");
      }
      const previousBrowserAt = session.lastRelevantBrowserAt;
      const previousInterventionAt = session.lastRelevantInterventionAt;
      const isBrowserSignal = signal === "browser";
      const isInterventionSignal = signal === "intervention";
      const toolActivity = commandActivity.get(observedSessionId);
      const pendingPromptUntil = pendingPromptSteering.get(observedSessionId) || 0;
      const pendingPromptActive = pendingPromptUntil > now;
      const recentToolBrowserActivity =
        toolActivity &&
        typeof toolActivity.lastCommandAt === "number" &&
        now - toolActivity.lastCommandAt < Math.max(config.reopenCooldownMs, config.browserEpisodeGapMs, 60000);
      if (signal === "browser") {
        session.lastRelevantBrowserAt = now;
      } else if (signal === "intervention") {
        session.lastRelevantInterventionAt = now;
      }

      if (isBrowserSignal && config.forceDelegateOnBrowserSignal && !session.delegatePrimed && now - session.lastDelegatePrimeAt >= 5000) {
        session.lastDelegatePrimeAt = now;
        const eventTaskText = (() => {
          const candidates = [
            event?.intent,
            event?.summary,
            event?.message,
            event?.payload?.intent,
            event?.payload?.summary_text,
            event?.payload?.message,
          ];
          for (const candidate of candidates) {
            if (typeof candidate === "string" && candidate.trim()) {
              return candidate.trim();
            }
          }
          return "Open and inspect the requested page in a live browser view.";
        })();
        try {
          let autoStartLatencyMs = null;
          const result = await browserCommandWithAutoStart({
            command,
            startApp: ensureStarted,
            waitForHealth,
            payload: {
              observed_session_id: observedSessionId,
              project_directory: extractProjectDirectory(event, directory),
              command_id: `begin_${observedSessionId}_${now}`,
              command: "begin_task",
              task_text: eventTaskText,
            },
            config,
            log,
            onAutoStartComplete: ({ startupLatencyMs }) => {
              autoStartLatencyMs = startupLatencyMs;
            },
          });
          if (typeof autoStartLatencyMs === "number") {
            void fireUiTelemetry({
              sessionId: session.lumonSessionId,
              event: "auto_start_completed",
              meta: { startup_latency_ms: autoStartLatencyMs, phase: "delegate_prime" },
            });
          }
          const primed =
            result &&
            (result.status === "success" ||
              result.status === "blocked" ||
              result.evidence?.frame_emitted === true);
          if (primed) {
            session.delegatePrimed = true;
            recentDelegateFailures = 0;
          }
          if (result?.open_url && shouldOpenForBrowserCommandResult(result)) {
            session.lastOpenedAt = now;
            void fireUiTelemetry({
              sessionId: session.lumonSessionId,
              event: "open_requested",
              meta: { reason_code: "delegate_prime", signal: "browser", command: "begin_task" },
            });
            try {
              await openUrl(result.open_url);
              void fireUiTelemetry({
                sessionId: session.lumonSessionId,
                event: "open_completed",
                meta: { reason_code: "delegate_prime", signal: "browser", command: "begin_task" },
              });
            } catch (openError) {
              void fireUiTelemetry({
                sessionId: session.lumonSessionId,
                event: "open_failed",
                meta: {
                  reason_code: "delegate_prime",
                  signal: "browser",
                  command: "begin_task",
                  message: String(openError?.message || openError || ""),
                },
              });
              throw openError;
            }
          }
        } catch (error) {
          await log(`Lumon delegate priming failed: ${String(error?.message || error || "unknown error")}`);
          recentDelegateFailures += 1;
          if (recentDelegateFailures >= 2) {
            await log("Lumon has failed to prime the browser delegate repeatedly. Run `./lumon triage` to collect runtime and log state.");
          }
        }
      }

      const canOpenForSignal =
        isInterventionSignal ||
        (isBrowserSignal &&
          !pendingPromptActive &&
          !recentToolBrowserActivity &&
          (session.delegatePrimed || !config.forceDelegateOnBrowserSignal));
        if (canOpenForSignal && shouldOpenForEvent(event, config.openPolicy)) {
        const episodeGapMs = isInterventionSignal ? config.interventionEpisodeGapMs : config.browserEpisodeGapMs;
        const previousRelevantAt = isInterventionSignal ? previousInterventionAt : previousBrowserAt;
        const openSignalKey = buildOpenSignalKey(event, signal, session.openUrl);
        const reopenCooldownMs = isInterventionSignal
          ? Math.min(config.reopenCooldownMs, config.interventionEpisodeGapMs)
          : config.reopenCooldownMs;
        const isNewEpisode =
          previousRelevantAt === 0 ||
          now - previousRelevantAt >= episodeGapMs ||
          now - session.lastOpenedAt >= episodeGapMs;
        const outsideCooldown = session.lastOpenedAt === 0 || now - session.lastOpenedAt >= reopenCooldownMs;
        debugTrace("openSignal", {
          observedSessionId,
          signal,
          eventType: eventTypeOf(event),
          previousRelevantAt,
          lastOpenedAt: session.lastOpenedAt,
          isNewEpisode,
          outsideCooldown,
          episodeGapMs,
          reopenCooldownMs,
        });

        if (
          openSignalKey &&
          session.lastOpenSignalKey === openSignalKey &&
          now - session.lastOpenSignalAt < OPEN_SIGNAL_DEDUPE_WINDOW_MS
        ) {
          debugTrace("openSignal.suppressed_duplicate_signal", {
            observedSessionId,
            signal,
            eventType: eventTypeOf(event),
            url: session.openUrl,
          });
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "duplicate_signal", signal, event_type: eventTypeOf(event) },
          });
          return;
        }

        if ((session.lastOpenedAt === 0 || isNewEpisode) && outsideCooldown && !session.openInProgress) {
          const shouldRefreshAttachment =
            typeof session.attachedAt !== "number" ||
            now - session.attachedAt >= Math.max(config.reopenCooldownMs, 15000);
          const refreshedSession = shouldRefreshAttachment
            ? await ensureAttached(event, directory, { forceRefresh: true })
            : null;
          const openTarget = refreshedSession || session;
          if (isDirectTakeoverActiveContext(null, openTarget)) {
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_suppressed",
              meta: { reason_code: "direct_takeover_mode", signal, event_type: eventTypeOf(event) },
            });
            return;
          }
          if (openTarget.uiConnected === true && openTarget.openUrl === session.openUrl) {
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_suppressed",
              meta: { reason_code: "already_visible", signal, event_type: eventTypeOf(event) },
            });
            return;
          }
          session.lastOpenedAt = now;
          openTarget.lastOpenedAt = now;
          session.lastOpenSignalKey = openSignalKey;
          session.lastOpenSignalAt = now;
          openTarget.lastOpenSignalKey = openSignalKey;
          openTarget.lastOpenSignalAt = now;
          session.openInProgress = true;
          openTarget.openInProgress = true;
          debugTrace("openSignal.opening", { observedSessionId, signal, url: openTarget.openUrl });
          void fireUiTelemetry({
            sessionId: openTarget.lumonSessionId,
            event: "open_requested",
            meta: { reason_code: signal, signal, event_type: eventTypeOf(event) },
          });
          try {
            await openUrl(openTarget.openUrl);
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_completed",
              meta: { reason_code: signal, signal, event_type: eventTypeOf(event) },
            });
          } catch (error) {
            debugTrace("openSignal.open_failed", {
              observedSessionId,
              signal,
              url: openTarget.openUrl,
              message: String(error?.message || error || ""),
            });
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_failed",
              meta: {
                reason_code: signal,
                signal,
                event_type: eventTypeOf(event),
                message: String(error?.message || error || ""),
              },
            });
            await log(`Lumon observed browser work but could not open the UI automatically. Open Lumon manually if needed: ${openTarget.openUrl}`);
          } finally {
            session.openInProgress = false;
            openTarget.openInProgress = false;
          }
        } else if ((session.lastOpenedAt === 0 || isNewEpisode) && outsideCooldown && session.openInProgress) {
          debugTrace("openSignal.suppressed_open_in_progress", {
            observedSessionId,
            signal,
            eventType: eventTypeOf(event),
          });
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "open_in_progress", signal, event_type: eventTypeOf(event) },
          });
        } else if (!outsideCooldown) {
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "cooldown", signal, event_type: eventTypeOf(event) },
          });
        } else if (session.lastOpenedAt !== 0 && !isNewEpisode) {
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "same_episode", signal, event_type: eventTypeOf(event) },
          });
        }
      } else if (isBrowserSignal && recentToolBrowserActivity) {
        debugTrace("openSignal.suppressed_tool_active", {
          observedSessionId,
          eventType: eventTypeOf(event),
          lastCommandAt: toolActivity.lastCommandAt,
        });
        void fireUiTelemetry({
          sessionId: session.lumonSessionId,
          event: "open_suppressed",
          meta: { reason_code: "tool_active", signal: "browser", event_type: eventTypeOf(event) },
        });
      } else if (isBrowserSignal && pendingPromptActive) {
        debugTrace("openSignal.suppressed_pending_tool", {
          observedSessionId,
          eventType: eventTypeOf(event),
          pendingPromptUntil,
        });
        void fireUiTelemetry({
          sessionId: session.lumonSessionId,
          event: "open_suppressed",
          meta: { reason_code: "pending_tool", signal: "browser", event_type: eventTypeOf(event) },
        });
      }
    }

    if (session && isTerminalEvent(event)) {
      sessions.delete(observedSessionId);
      inflight.delete(observedSessionId);
      commandActivity.delete(observedSessionId);
      pendingPromptSteering.delete(observedSessionId);
      activeBrowserTasks.delete(observedSessionId);
      stopResumePollerIfIdle();
    }
  }

  return {
    sessions,
    ensureAutoResumePolling: ensureResumePoller,
    stopAutoResumePollingIfIdle: stopResumePollerIfIdle,
    upsertSessionFromTool,
    markSessionStopped,
    handleEvent,
  };
}
