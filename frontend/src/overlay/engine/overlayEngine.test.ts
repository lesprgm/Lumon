import { afterEach, describe, expect, it, vi } from "vitest";

import { OverlayEngine } from "./overlayEngine";
import { getSpriteSet } from "../sprites";
import type { AgentEventPayload, FramePayload, SessionStatePayload } from "../../protocol/types";

const PLAYWRIGHT_CAPABILITIES = {
  supports_pause: true,
  supports_approval: true,
  supports_takeover: true,
  supports_frames: true,
};

function makeSessionState(state: SessionStatePayload["state"]): SessionStatePayload {
  return {
    session_id: "sess_demo_001",
    adapter_id: "playwright_native",
    adapter_run_id: "run_demo_001",
    state,
    interaction_mode: state === "waiting_for_approval" ? "approval" : state === "takeover" ? "takeover" : "watch",
    active_checkpoint_id: null,
    task_text: "Find a hotel",
    viewport: { width: 1280, height: 800 },
    capabilities: PLAYWRIGHT_CAPABILITIES,
  };
}

function makeFrame(frameSeq: number): FramePayload {
  return {
    mime_type: "image/jpeg",
    data_base64: "AAA",
    frame_seq: frameSeq,
  };
}

function makeEvent(eventSeq: number): AgentEventPayload {
  return {
    event_seq: eventSeq,
    event_id: `evt_${eventSeq}`,
    source_event_id: `src_${eventSeq}`,
    timestamp: new Date().toISOString(),
    session_id: "sess_demo_001",
    adapter_id: "playwright_native",
    adapter_run_id: "run_demo_001",
    agent_id: "main_001",
    parent_agent_id: null,
    agent_kind: "main",
    environment_id: "env_browser_main",
    visibility_mode: "foreground",
    action_type: "click",
    state: "clicking",
    summary_text: `Click ${eventSeq}`,
    intent: "Click something",
    risk_level: "none",
    subagent_source: null,
    cursor: { x: 100 + eventSeq, y: 200 },
    target_rect: { x: 80, y: 160, width: 40, height: 30 },
    meta: {},
  };
}

describe("OverlayEngine", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("buffers events until the stage becomes ready while showing the latest frame", () => {
    const engine = new OverlayEngine();
    let latest = {
      frameSrc: null as string | null,
      mainAgent: null as { summaryText: string; framePath: string } | null,
      targetPoint: null as { x: number; y: number } | null,
    };
    engine.subscribe((snapshot) => {
      latest = {
        frameSrc: snapshot.frameSrc,
        mainAgent: snapshot.mainAgent
          ? { summaryText: snapshot.mainAgent.summaryText, framePath: snapshot.mainAgent.framePath }
          : null,
        targetPoint: snapshot.targetPoint,
      };
    });

    engine.applySessionState(makeSessionState("running"));
    engine.enqueueFrame(makeFrame(1));
    engine.enqueueEvent(makeEvent(1));

    expect(latest.frameSrc).toContain("data:image/jpeg;base64,AAA");
    expect(latest.mainAgent).toBeNull();

    engine.setStageReady(true);

    expect(latest.frameSrc).toContain("data:image/jpeg;base64,AAA");
    expect(latest.mainAgent?.summaryText).toBe("Click 1");
    expect(latest.targetPoint).toEqual({ x: 101, y: 200 });
  });

  it("drops stale frames and keeps the newest buffered frames", () => {
    const engine = new OverlayEngine();
    let latestFrameSrc: string | null = null;
    engine.subscribe((snapshot) => {
      latestFrameSrc = snapshot.frameSrc;
    });
    engine.setStageReady(true);
    engine.enqueueFrame(makeFrame(2));
    engine.enqueueFrame(makeFrame(1));
    expect(latestFrameSrc).toContain("data:image/jpeg;base64,AAA");
    engine.enqueueFrame(makeFrame(3));
    expect(latestFrameSrc).toContain("data:image/jpeg;base64,AAA");
  });

  it("resets back to the initial idle placeholder state", () => {
    const engine = new OverlayEngine();
    let latest = {
      frameSrc: null as string | null,
      stageReady: false,
      caption: "",
      sessionState: "",
      mainAgentSummary: null as string | null,
    };
    engine.subscribe((snapshot) => {
      latest = {
        frameSrc: snapshot.frameSrc,
        stageReady: snapshot.stageReady,
        caption: snapshot.caption,
        sessionState: snapshot.sessionState,
        mainAgentSummary: snapshot.mainAgent?.summaryText ?? null,
      };
    });

    engine.applySessionState(makeSessionState("running"));
    engine.setStageReady(true);
    engine.enqueueFrame(makeFrame(1));
    engine.enqueueEvent(makeEvent(1));

    engine.reset();

    expect(latest).toEqual({
      frameSrc: null,
      stageReady: false,
      caption: "Awaiting run",
      sessionState: "idle",
      mainAgentSummary: null,
    });
  });

  it("switches sprite families without dropping the current scene", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let latestFramePath = "";
    engine.subscribe((snapshot) => {
      latestFramePath = snapshot.mainAgent?.framePath ?? "";
    });
    engine.setStageReady(true);
    engine.applySessionState(makeSessionState("running"));
    engine.enqueueEvent(makeEvent(1));
    engine.tick(now);
    expect(latestFramePath).toContain("/assets/lobster/");

    now = 16;
    engine.setSpriteSet(getSpriteSet("dog"));
    engine.tick(now);
    expect(latestFramePath).toContain("/assets/dog/");
  });

  it("maps completed session state to success animation then returns to idle", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let latestFramePath = "";
    engine.subscribe((snapshot) => {
      latestFramePath = snapshot.mainAgent?.framePath ?? "";
    });
    engine.setStageReady(true);
    engine.applySessionState(makeSessionState("completed"));
    engine.enqueueEvent({ ...makeEvent(4), action_type: "complete", state: "done", summary_text: "Done" });
    engine.tick(now);
    expect(latestFramePath).toContain("success/");
    now = 2000;
    engine.tick(now);
    expect(latestFramePath).toContain("idle/");
  });

  it("keeps only the newest buffered frame and event windows before stage readiness", () => {
    const engine = new OverlayEngine();
    let latestSummary = "";
    let latestFrameSrc: string | null = null;

    engine.subscribe((snapshot) => {
      latestSummary = snapshot.mainAgent?.summaryText ?? "";
      latestFrameSrc = snapshot.frameSrc;
    });

    for (let frameSeq = 1; frameSeq <= 5; frameSeq += 1) {
      engine.enqueueFrame(makeFrame(frameSeq));
    }
    for (let eventSeq = 1; eventSeq <= 55; eventSeq += 1) {
      engine.enqueueEvent(makeEvent(eventSeq));
    }

    expect(latestFrameSrc).toContain("data:image/jpeg;base64,AAA");

    engine.setStageReady(true);

    expect(latestFrameSrc).toContain("data:image/jpeg;base64,AAA");
    expect(latestSummary).toBe("Click 55");
  });

  it("removes same-scene subagents after the result animation window", () => {
    vi.useFakeTimers();
    const engine = new OverlayEngine();
    let subagentCount = 0;
    engine.subscribe((snapshot) => {
      subagentCount = snapshot.subagents.length;
    });
    engine.setStageReady(true);

    engine.enqueueEvent({
      ...makeEvent(10),
      agent_id: "subagent_001",
      parent_agent_id: "main_001",
      agent_kind: "same_scene_subagent",
      visibility_mode: "same_scene_visible",
      action_type: "spawn_subagent",
      state: "handoff",
      summary_text: "Spawn helper",
      subagent_source: "simulated",
    });
    expect(subagentCount).toBe(1);

    engine.enqueueEvent({
      ...makeEvent(11),
      agent_id: "subagent_001",
      parent_agent_id: "main_001",
      agent_kind: "same_scene_subagent",
      visibility_mode: "same_scene_visible",
      action_type: "subagent_result",
      state: "done",
      summary_text: "Helper done",
      subagent_source: "simulated",
    });

    vi.advanceTimersByTime(599);
    expect(subagentCount).toBe(1);
    vi.advanceTimersByTime(1);
    expect(subagentCount).toBe(0);
  });

  it("clears transient main action animations after the hold window", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let actionType: string | null = null;
    let typing = false;

    engine.subscribe((snapshot) => {
      actionType = snapshot.mainActionType;
      typing = snapshot.typing;
    });
    engine.setStageReady(true);
    engine.enqueueEvent({ ...makeEvent(20), action_type: "type", state: "typing" });

    expect(actionType).toBe("type");
    expect(typing).toBe(true);

    now = 300;
    engine.tick(now);
    expect(actionType).toBe("type");

    now = 500;
    engine.tick(now);
    expect(actionType).toBeNull();
    expect(typing).toBe(false);
  });

  it("does not immediately downgrade a higher-priority main action during a dense burst", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let actionType: string | null = null;

    engine.subscribe((snapshot) => {
      actionType = snapshot.mainActionType;
    });
    engine.setStageReady(true);

    engine.enqueueEvent({ ...makeEvent(30), action_type: "click", state: "clicking" });
    expect(actionType).toBe("click");

    now = 40;
    engine.enqueueEvent({ ...makeEvent(31), action_type: "read", state: "reading" });
    expect(actionType).toBe("click");

    now = 350;
    engine.tick(now);
    expect(actionType).toBeNull();
  });

  it("smooths main-agent movement over multiple animation frames", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let mainAgentX = 0;

    engine.subscribe((snapshot) => {
      mainAgentX = snapshot.mainAgent?.x ?? 0;
    });
    engine.setStageReady(true);

    engine.enqueueEvent({ ...makeEvent(40), cursor: { x: 100, y: 200 } });
    expect(mainAgentX).toBe(128);

    now = 16;
    engine.enqueueEvent({ ...makeEvent(41), cursor: { x: 150, y: 200 } });
    expect(mainAgentX).toBe(128);

    now = 32;
    engine.tick(now);
    expect(mainAgentX).toBeGreaterThan(128);
    expect(mainAgentX).toBeLessThan(178);

    for (let step = 0; step < 40; step += 1) {
      now += 16;
      engine.tick(now);
    }
    expect(Math.round(mainAgentX)).toBe(178);
  });

  it("uses explicit local-glide and teleport-arrive movement states", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let latestMovementState = "";
    let latestAgentX = 0;

    engine.subscribe((snapshot) => {
      latestMovementState = snapshot.mainAgent?.movementState ?? "";
      latestAgentX = snapshot.mainAgent?.x ?? 0;
    });
    engine.setStageReady(true);

    engine.enqueueEvent({ ...makeEvent(60), cursor: { x: 100, y: 200 } });
    expect(latestMovementState).toBe("anchored");
    expect(latestAgentX).toBe(128);

    now = 16;
    engine.enqueueEvent({ ...makeEvent(61), cursor: { x: 135, y: 200 } });
    expect(latestMovementState).toBe("local_glide");
    expect(latestAgentX).toBe(128);

    now = 32;
    engine.enqueueEvent({ ...makeEvent(62), cursor: { x: 420, y: 240 } });
    expect(latestMovementState).toBe("teleport_arrive");
    expect(latestAgentX).toBeGreaterThan(420);
  });

  it("keeps read and type actions anchored unless the hotspot meaningfully changes", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let latestAgentX = 0;
    let latestMovementState = "";

    engine.subscribe((snapshot) => {
      latestAgentX = snapshot.mainAgent?.x ?? 0;
      latestMovementState = snapshot.mainAgent?.movementState ?? "";
    });
    engine.setStageReady(true);

    engine.enqueueEvent({ ...makeEvent(70), action_type: "type", cursor: { x: 300, y: 260 } });
    expect(latestAgentX).toBe(328);
    expect(latestMovementState).toBe("anchored");

    now = 24;
    engine.enqueueEvent({ ...makeEvent(71), action_type: "read", cursor: { x: 308, y: 262 } });
    expect(latestAgentX).toBe(328);
    expect(latestMovementState).toBe("anchored");
  });

  it("coalesces dense nearby updates instead of retargeting every burst", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const engine = new OverlayEngine();
    let latestAgentX = 0;

    engine.subscribe((snapshot) => {
      latestAgentX = snapshot.mainAgent?.x ?? 0;
    });
    engine.setStageReady(true);

    engine.enqueueEvent({ ...makeEvent(80), cursor: { x: 100, y: 200 } });
    now = 16;
    engine.enqueueEvent({ ...makeEvent(81), cursor: { x: 130, y: 200 } });
    now = 32;
    engine.enqueueEvent({ ...makeEvent(82), cursor: { x: 146, y: 200 } });

    for (let step = 0; step < 40; step += 1) {
      now += 16;
      engine.tick(now);
    }

    expect(Math.round(latestAgentX)).toBe(158);
  });

  it("keeps a precise hotspot marker while offsetting the sprite away from it", () => {
    const engine = new OverlayEngine();
    let latestTargetPoint: { x: number; y: number } | null = null;
    let latestAgentX = 0;
    let latestAgentY = 0;

    engine.subscribe((snapshot) => {
      latestTargetPoint = snapshot.targetPoint;
      latestAgentX = snapshot.mainAgent?.x ?? 0;
      latestAgentY = snapshot.mainAgent?.y ?? 0;
    });
    engine.setStageReady(true);

    engine.enqueueEvent({
      ...makeEvent(50),
      cursor: { x: 420, y: 260 },
      target_rect: { x: 400, y: 240, width: 40, height: 40 },
    });

    expect(latestTargetPoint).toEqual({ x: 420, y: 260 });
    expect(latestAgentX).not.toBe(420);
    expect(latestAgentY).not.toBe(260);
    expect(latestAgentX).toBeGreaterThan(420);
  });
});
