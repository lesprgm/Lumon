// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LiveStage, resolveCaptionLayout, resolveMainSpriteStyle } from "./LiveStage";
import {
  StatusBar,
  readingMiddleFrameIndex,
  resolveTopBarDisplayedFrameIndex,
  resolveTopBarIdleFrameDelayMs,
} from "./StatusBar";
import type { SceneSnapshot } from "../overlay/engine/overlayEngine";
import { initialSessionStoreState } from "../store/sessionStore";
import type { AdapterCapabilities, BrowserContextPayload, SessionStatePayload } from "../protocol/types";

const SNAPSHOT: SceneSnapshot = {
  frameSrc: null,
  stageReady: true,
  sessionState: "running",
  mainActionType: null,
  caption: "",
  mainAgent: null,
  subagents: [],
  ripples: [],
  targetPoint: null,
  targetRect: null,
  typing: false,
  fallbackMode: false,
};

const BROWSER_CONTEXT: BrowserContextPayload = {
  session_id: "sess_test",
  adapter_id: "playwright_native",
  adapter_run_id: "run_test",
  url: "https://www.wikipedia.org",
  title: "Wikipedia",
  domain: "www.wikipedia.org",
  environment_type: "external",
  timestamp: "2026-03-20T12:00:00Z",
};

const CAPABILITIES: AdapterCapabilities = {
  supports_pause: true,
  supports_approval: true,
  supports_takeover: true,
  supports_frames: true,
};

const SESSION: SessionStatePayload = {
  session_id: "sess_test",
  adapter_id: "playwright_native",
  adapter_run_id: "run_test",
  state: "running",
  interaction_mode: "watch",
  active_checkpoint_id: null,
  task_text: "Open Wikipedia and inspect the page",
  viewport: { width: 1280, height: 800 },
  capabilities: CAPABILITIES,
};

describe("LiveStage takeover control", () => {
  it("shows region-specific takeover comments for the current top-bar perch", () => {
    const randomSpy = vi.spyOn(Math, "random").mockReturnValue(0);

    const takeoverSession: SessionStatePayload = {
      ...SESSION,
      state: "takeover",
      interaction_mode: "takeover",
    };

    const { container } = render(
      <StatusBar
        state={{ ...initialSessionStoreState, connectionState: "connected", session: takeoverSession }}
        leftRailCollapsed={false}
        onToggleLeftRail={() => {}}
        spriteFamily="lobster"
        onSpriteFamilyChange={() => {}}
      />,
    );

    const sprite = container.querySelector(".status-idle-sprite");
    expect(sprite).not.toBeNull();
    expect(sprite?.getAttribute("data-status-sprite-mode")).toBe("takeover");
    expect(sprite?.getAttribute("data-status-sprite-emote")).toBe("reading");
    expect(sprite?.getAttribute("data-status-stop-region")).toBe("center");
    expect(sprite?.getAttribute("src")).toContain("/reading/");
    expect(sprite?.getAttribute("style")).toContain("left: 50%");

    fireEvent.mouseEnter(sprite as Element);

    expect(container.querySelector(".status-thought-bubble")?.textContent).toBe("you know i can do this better right?");

    randomSpy.mockRestore();
  });

  it("shows the takeover top-bar sprite when interaction mode is takeover even if session state is still running", () => {
    const { container } = render(
      <StatusBar
        state={{
          ...initialSessionStoreState,
          connectionState: "connected",
          session: { ...SESSION, state: "running", interaction_mode: "takeover" },
        }}
        leftRailCollapsed={false}
        onToggleLeftRail={() => {}}
        spriteFamily="lobster"
        onSpriteFamilyChange={() => {}}
      />,
    );

    const sprite = container.querySelector(".status-idle-sprite");
    expect(sprite).not.toBeNull();
    expect(sprite?.getAttribute("data-status-sprite-mode")).toBe("takeover");
  });

  it("shows the guaranteed rentahuman comment on takeover entry", () => {
    vi.useFakeTimers();
    const randomSpy = vi.spyOn(Math, "random").mockReturnValue(0);

    const { container } = render(
      <StatusBar
        state={{
          ...initialSessionStoreState,
          connectionState: "connected",
          session: { ...SESSION, state: "takeover", interaction_mode: "takeover" },
        }}
        leftRailCollapsed={false}
        onToggleLeftRail={() => {}}
        spriteFamily="lobster"
        onSpriteFamilyChange={() => {}}
      />,
    );

    expect(container.querySelector(".status-thought-bubble")?.textContent).toBe(
      "rentahuman might definitely need you.",
    );

    act(() => {
      vi.advanceTimersByTime(2400);
    });

    expect(container.querySelector(".status-thought-bubble")?.textContent).toBe(
      "rentahuman might definitely need you.",
    );

    act(() => {
      vi.advanceTimersByTime(1200);
    });

    expect(container.querySelector(".status-thought-bubble")).toBeNull();

    randomSpy.mockRestore();
    vi.useRealTimers();
  });

  it("holds the takeover reading animation on its middle frame for about nine seconds", () => {
    expect(readingMiddleFrameIndex(8)).toBe(4);
    expect(
      resolveTopBarDisplayedFrameIndex({
        isTakeoverTopBarSprite: true,
        movementPhase: "perched",
        idleEmote: "reading",
        frameCount: 8,
        idleFrameIndex: 0,
      }),
    ).toBe(4);
    expect(
      resolveTopBarIdleFrameDelayMs({
        isTakeoverTopBarSprite: true,
        movementPhase: "perched",
        idleEmote: "reading",
        frameCount: 8,
        idleFrameIndex: 4,
        baseFrameDurationMs: 340,
      }),
    ).toBe(9000);
    expect(
      resolveTopBarIdleFrameDelayMs({
        isTakeoverTopBarSprite: true,
        movementPhase: "moving",
        idleEmote: "reading",
        frameCount: 8,
        idleFrameIndex: 4,
        baseFrameDurationMs: 340,
      }),
    ).toBe(340);
    expect(
      resolveTopBarIdleFrameDelayMs({
        isTakeoverTopBarSprite: false,
        movementPhase: "perched",
        idleEmote: "reading",
        frameCount: 8,
        idleFrameIndex: 4,
        baseFrameDurationMs: 340,
      }),
    ).toBe(340);
  });

  it("renders takeover reading on the held middle frame immediately", () => {
    const { container } = render(
      <StatusBar
        state={{
          ...initialSessionStoreState,
          connectionState: "connected",
          session: { ...SESSION, state: "takeover", interaction_mode: "takeover" },
        }}
        leftRailCollapsed={false}
        onToggleLeftRail={() => {}}
        spriteFamily="lobster"
        onSpriteFamilyChange={() => {}}
      />,
    );

    const sprite = container.querySelector(".status-idle-sprite");
    expect(sprite?.getAttribute("data-status-sprite-frame")).toBe("4");
    expect(sprite?.getAttribute("src")).toContain("reading_04.png");
    expect(sprite?.className).toContain("status-idle-sprite-is-held");
  });

  it("dispatches start_takeover from the top browser chrome when the header is present", () => {
    const onCommand = vi.fn();

    render(
      <div className="app-shell">
        <StatusBar
          state={{ ...initialSessionStoreState, connectionState: "connected", session: SESSION }}
          leftRailCollapsed={false}
          onToggleLeftRail={() => {}}
          spriteFamily="lobster"
          onSpriteFamilyChange={() => {}}
        />
        <main className="main-grid">
          <section className="stage-workspace">
            <LiveStage
              snapshot={SNAPSHOT}
              onStageReady={() => {}}
              hasAgentActivity={false}
              adapterId="playwright_native"
              taskText={SESSION.task_text}
              supportsFrames
              videoStream={null}
              videoStatus="idle"
              frameFps={null}
              isNavigating={false}
              activeIntervention={null}
              browserContext={BROWSER_CONTEXT}
              capabilities={CAPABILITIES}
              interactionMode="watch"
              observerMode={false}
              reviewMode={false}
              onCommand={onCommand}
              sessionStatus="running"
            />
          </section>
        </main>
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Take over" }));

    expect(onCommand).toHaveBeenCalledWith({ type: "start_takeover", payload: {} });
  });

  it("hides the activity toggle in the status bar when live activity is suppressed", () => {
    const view = render(
      <StatusBar
        state={{ ...initialSessionStoreState, connectionState: "connected", session: SESSION }}
        leftRailCollapsed={true}
        onToggleLeftRail={() => {}}
        showActivityToggle={false}
        spriteFamily="lobster"
        onSpriteFamilyChange={() => {}}
      />,
    );

    expect(view.container.querySelector(".rail-toggle")).toBeNull();
  });

  it("keeps the top browser chrome interactive and out from under the header lane", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(/--lumon-top-ui-clearance:\s*58px;/);
    expect(css).toMatch(/\.stage-chrome-top\s*\{[^}]*top:\s*var\(--lumon-top-ui-clearance\)/s);
    expect(css).toMatch(/\.stage-browser-shell\s*\{[^}]*pointer-events:\s*auto;/s);
    expect(css).toMatch(/\.stage-browser-shell\s*\{[^}]*border-top:\s*none;/s);
  });

  it("perches the typing sprite on the top-left edge of the active input", () => {
    const style = resolveMainSpriteStyle(
      {
        mainAgent: {
          id: "main_001",
          x: 420,
          y: 260,
          framePath: "/assets/lobster/idle/frames/idle_00.png",
          kind: "main",
          summaryText: "Typing",
          movementState: "anchored",
        },
        typing: true,
        targetRect: { x: 400, y: 240, width: 120, height: 32 },
      },
      { width: 1920, height: 1080 },
    );

    expect(style).toEqual({ left: 394, top: 190 });
  });

  it("attaches the caption bubble to the sprite instead of the target hotspot", () => {
    const layout = resolveCaptionLayout(
      {
        fallbackMode: false,
        mainAgent: {
          id: "main_001",
          x: 420,
          y: 260,
          framePath: "/assets/lobster/idle/frames/idle_00.png",
          kind: "main",
          summaryText: "Typing",
          movementState: "anchored",
        },
      },
      { width: 1920, height: 1080 },
      { left: 394, top: 190 },
    );

    expect(layout).not.toBeNull();
    expect(layout?.bubbleStyle.left).toBe("454px");
    expect(layout?.bubbleStyle.top).toBe("210px");
    expect(layout?.bubbleClassName).toContain("is-right");
    expect(layout?.tailStyle).toEqual({ left: "420px", top: "260px" });
  });

  it("shows only the typing indicator while typing into a live field", () => {
    vi.stubEnv("VITE_LUMON_OVERLAY_SPRITES", "true");
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      width: 1280,
      height: 800,
      top: 0,
      left: 0,
      right: 1280,
      bottom: 800,
      toJSON: () => ({}),
    });

    const typingSnapshot: SceneSnapshot = {
      ...SNAPSHOT,
      mainAgent: {
        id: "main_001",
        x: 420,
        y: 260,
        framePath: "/assets/lobster/idle/frames/idle_00.png",
        kind: "main",
        summaryText: "Typing",
        movementState: "anchored",
      },
      targetRect: { x: 400, y: 240, width: 120, height: 32 },
      typing: true,
      caption: "Typing into input[name='search']",
    };

    const view = render(
      <LiveStage
        snapshot={typingSnapshot}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="watch"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="running"
      />,
    );

    expect(view.container.querySelector(".typing-bubble")).not.toBeNull();
    expect(view.container.querySelector(".caption-bubble")).toBeNull();

    rectSpy.mockRestore();
    vi.unstubAllEnvs();
  });

  it("hides in-stage sprites during takeover while keeping takeover chrome active", () => {
    vi.stubEnv("VITE_LUMON_OVERLAY_SPRITES", "true");
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      width: 1280,
      height: 800,
      top: 0,
      left: 0,
      right: 1280,
      bottom: 800,
      toJSON: () => ({}),
    });
    const takeoverSnapshot: SceneSnapshot = {
      ...SNAPSHOT,
      mainAgent: {
        id: "main_001",
        x: 420,
        y: 260,
        framePath: "/assets/lobster/idle/frames/idle_00.png",
        kind: "main",
        summaryText: "Watching",
        movementState: "anchored",
      },
      ripples: [{ x: 420, y: 260, createdAt: 1 }],
    };

    const view = render(
      <LiveStage
        snapshot={takeoverSnapshot}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="takeover"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="takeover"
      />,
    );

    view.rerender(
      <LiveStage
        snapshot={{ ...takeoverSnapshot }}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="takeover"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="takeover"
      />,
    );

    expect(view.container.querySelector('.sprite-positioner-main')).toBeNull();
    expect(view.container.querySelector('.sprite-positioner-subagent')).toBeNull();
    expect(view.container.querySelector('.click-ripple')).toBeNull();
    expect(view.container.querySelector('.intervention-origin-pulse')).toBeNull();
    expect(view.container.querySelector('.intervention-link')).toBeNull();
    expect(view.getAllByRole("button", { name: "Return control" }).length).toBeGreaterThan(0);

    rectSpy.mockRestore();
    vi.unstubAllEnvs();
  });

  it("collapses takeover controls into a bottom-right chip and can expand them again", () => {
    const onCommand = vi.fn();

    const view = render(
      <LiveStage
        snapshot={SNAPSHOT}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="takeover"
        observerMode={false}
        reviewMode={false}
        onCommand={onCommand}
        sessionStatus="takeover"
      />,
    );

    const scoped = within(view.container);

    fireEvent.click(scoped.getByRole("button", { name: "Collapse manual control card" }));

    expect(view.container.querySelector(".takeover-chip")).not.toBeNull();
    expect(scoped.getByText("Manual control active")).toBeTruthy();

    fireEvent.click(scoped.getByRole("button", { name: "Expand manual control card" }));

    expect(view.container.querySelector(".takeover-chip")).toBeNull();
    expect(scoped.getByText("You are in control")).toBeTruthy();
  });

  it("shows the in-stage main sprite on first mount in watch mode", () => {
    vi.stubEnv("VITE_LUMON_OVERLAY_SPRITES", "true");
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      width: 1280,
      height: 800,
      top: 0,
      left: 0,
      right: 1280,
      bottom: 800,
      toJSON: () => ({}),
    });
    const watchSnapshot: SceneSnapshot = {
      ...SNAPSHOT,
      mainAgent: {
        id: "main_001",
        x: 420,
        y: 260,
        framePath: "/assets/lobster/idle/frames/idle_00.png",
        kind: "main",
        summaryText: "Watching",
        movementState: "anchored",
      },
    };

    const view = render(
      <LiveStage
        snapshot={watchSnapshot}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="watch"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="running"
      />,
    );

    expect(view.container.querySelector(".sprite-positioner-main")).not.toBeNull();

    rectSpy.mockRestore();
    vi.unstubAllEnvs();
  });

  it("marks the stage not ready before the first frame when no browser evidence exists", () => {
    const onStageReady = vi.fn();

    const view = render(
      <LiveStage
        snapshot={SNAPSHOT}
        onStageReady={onStageReady}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={null}
        capabilities={CAPABILITIES}
        interactionMode="watch"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="running"
      />,
    );

    expect(onStageReady).toHaveBeenLastCalledWith(false);
  });

  it("shows the waiting-for-page reason before the first visible frame", () => {
    const liveSnapshot: SceneSnapshot = {
      ...SNAPSHOT,
      frameSrc: "/frames/live.png",
      mainAgent: {
        id: "main_001",
        x: 420,
        y: 260,
        framePath: "/assets/lobster/idle/frames/idle_00.png",
        kind: "main",
        summaryText: "Watching the page",
        movementState: "anchored",
      },
    };

    render(
      <LiveStage
        snapshot={liveSnapshot}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="watch"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="running"
      />,
    );

    expect(screen.getAllByText("Waiting for the first visible page").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Lumon stays quiet until there is a page worth watching.").length).toBeGreaterThan(0);
  });

  it("shows a bootstrap sprite when live browser evidence exists before agent activity", () => {
    vi.stubEnv("VITE_LUMON_OVERLAY_SPRITES", "true");
    const requestAnimationFrameSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const cancelAnimationFrameSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      width: 1280,
      height: 800,
      top: 0,
      left: 0,
      right: 1280,
      bottom: 800,
      toJSON: () => ({}),
    });

    const liveSnapshot: SceneSnapshot = {
      ...SNAPSHOT,
      frameSrc: "/frames/live.png",
      caption: "Watching the page",
    };

    const view = render(
      <LiveStage
        sessionId="sess_bootstrap"
        snapshot={liveSnapshot}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="watch"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        sessionStatus="running"
      />,
    );

    const sprite = view.container.querySelector('.sprite-positioner-main img[alt="Main sprite"]');
    expect(sprite).not.toBeNull();
    expect((sprite as HTMLImageElement).getAttribute("src")).toContain("/assets/");
    expect(view.container.querySelector(".sprite-positioner-main")).not.toBeNull();
    expect(requestAnimationFrameSpy).toHaveBeenCalled();

    view.unmount();
    expect(cancelAnimationFrameSpy).toHaveBeenCalledWith(1);

    rectSpy.mockRestore();
    requestAnimationFrameSpy.mockRestore();
    cancelAnimationFrameSpy.mockRestore();
    vi.unstubAllEnvs();
  });

  it("emits sprite_visible only after the sprite image loads", () => {
    vi.stubEnv("VITE_LUMON_OVERLAY_SPRITES", "true");
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      width: 1280,
      height: 800,
      top: 0,
      left: 0,
      right: 1280,
      bottom: 800,
      toJSON: () => ({}),
    });
    const onUiTelemetry = vi.fn();
    const liveSnapshot: SceneSnapshot = {
      ...SNAPSHOT,
      frameSrc: "/frames/live.png",
      caption: "Watching the page",
    };

    const view = render(
      <LiveStage
        sessionId="sess_bootstrap"
        snapshot={liveSnapshot}
        onStageReady={() => {}}
        hasAgentActivity={false}
        adapterId="playwright_native"
        taskText={SESSION.task_text}
        supportsFrames
        videoStream={null}
        videoStatus="idle"
        frameFps={null}
        isNavigating={false}
        activeIntervention={null}
        browserContext={BROWSER_CONTEXT}
        capabilities={CAPABILITIES}
        interactionMode="watch"
        observerMode={false}
        reviewMode={false}
        onCommand={() => {}}
        onUiTelemetry={onUiTelemetry}
        sessionStatus="running"
      />,
    );

    const sprite = view.container.querySelector('.sprite-positioner-main img[alt="Main sprite"]') as HTMLImageElement | null;
    expect(sprite).not.toBeNull();
    expect(onUiTelemetry).not.toHaveBeenCalled();

    act(() => {
      fireEvent.load(sprite as HTMLImageElement);
      fireEvent.load(sprite as HTMLImageElement);
    });

    expect(onUiTelemetry).toHaveBeenCalledTimes(1);
    expect(onUiTelemetry).toHaveBeenCalledWith({
      event: "sprite_visible",
      meta: { source_mode: "bootstrap" },
    });

    rectSpy.mockRestore();
    vi.unstubAllEnvs();
  });
});
