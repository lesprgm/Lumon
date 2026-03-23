import { useEffect, useMemo, useRef, useState } from "react";

import { getSpriteSet, SPRITE_FAMILY_OPTIONS, type SpriteFamily } from "../overlay/sprites";
import type { SessionStoreState } from "../store/sessionStore";

const DWELL_MIN_MS = 6000;
const DWELL_MAX_MS = 9000;
const CONSIDER_MOVE_MIN_MS = 320;
const CONSIDER_MOVE_MAX_MS = 520;
const SETTLE_MIN_MS = 850;
const SETTLE_MAX_MS = 1200;
const TAKEOVER_THOUGHT_DELAY_MIN_MS = 900;
const TAKEOVER_THOUGHT_DELAY_MAX_MS = 1400;
const TAKEOVER_THOUGHT_DURATION_MS = 2400;
const TAKEOVER_INITIAL_THOUGHT_DURATION_MS = 3600;
const TAKEOVER_READING_MIDDLE_FRAME_HOLD_MS = 9000;
const TAKEOVER_STOP_DWELL_MS = 9000;

const TOP_BAR_REGIONS = {
  logo: { minLane: 18, maxLane: 22 },
  center: { minLane: 47, maxLane: 53 },
  status: { minLane: 76, maxLane: 81 },
} as const;

type TakeoverRegion = keyof typeof TOP_BAR_REGIONS;
type TopBarEmote = "idle" | "busy" | "success" | "reading" | "locomotion";
type MovementPhase = "perched" | "considering_move" | "moving" | "settling";

function randomBetween(min: number, max: number): number {
  return Math.round(min + Math.random() * (max - min));
}

function randomLane(region: TakeoverRegion): number {
  const { minLane, maxLane } = TOP_BAR_REGIONS[region];
  return Math.round((minLane + Math.random() * (maxLane - minLane)) * 10) / 10;
}

function formatLane(lane: number): string {
  return `${lane.toFixed(1)}%`;
}

function moveDistance(a: number, b: number): number {
  return Math.abs(a - b);
}

function dwellDurationMs(region: TakeoverRegion, isTakeover: boolean): number {
  if (isTakeover) {
    void region;
    return TAKEOVER_STOP_DWELL_MS;
  }
  const base = randomBetween(DWELL_MIN_MS, DWELL_MAX_MS);
  if (region === "center") {
    return base + 600;
  }
  return base;
}

function settleDurationMs(): number {
  return randomBetween(SETTLE_MIN_MS, SETTLE_MAX_MS);
}

function considerDurationMs(): number {
  return randomBetween(CONSIDER_MOVE_MIN_MS, CONSIDER_MOVE_MAX_MS);
}

function thoughtDelayMs(): number {
  return randomBetween(TAKEOVER_THOUGHT_DELAY_MIN_MS, TAKEOVER_THOUGHT_DELAY_MAX_MS);
}

function moveDurationMs(fromLane: number, toLane: number): number {
  void fromLane;
  void toLane;
  return 10000;
}

export function readingMiddleFrameIndex(frameCount: number): number {
  return Math.floor(frameCount / 2);
}

function shouldFreezeTakeoverReadingPose({
  isTakeoverTopBarSprite,
  movementPhase,
  idleEmote,
  frameCount,
}: {
  isTakeoverTopBarSprite: boolean;
  movementPhase: MovementPhase;
  idleEmote: TopBarEmote;
  frameCount: number;
}): boolean {
  return (
    isTakeoverTopBarSprite &&
    movementPhase !== "moving" &&
    idleEmote === "reading" &&
    frameCount > 0
  );
}

export function resolveTopBarDisplayedFrameIndex({
  isTakeoverTopBarSprite,
  movementPhase,
  idleEmote,
  frameCount,
  idleFrameIndex,
}: {
  isTakeoverTopBarSprite: boolean;
  movementPhase: MovementPhase;
  idleEmote: TopBarEmote;
  frameCount: number;
  idleFrameIndex: number;
}): number {
  if (
    shouldFreezeTakeoverReadingPose({
      isTakeoverTopBarSprite,
      movementPhase,
      idleEmote,
      frameCount,
    })
  ) {
    return readingMiddleFrameIndex(frameCount);
  }

  if (frameCount <= 0) {
    return 0;
  }

  return idleFrameIndex % frameCount;
}

export function resolveTopBarIdleFrameDelayMs({
  isTakeoverTopBarSprite,
  movementPhase,
  idleEmote,
  frameCount,
  idleFrameIndex,
  baseFrameDurationMs,
}: {
  isTakeoverTopBarSprite: boolean;
  movementPhase: MovementPhase;
  idleEmote: TopBarEmote;
  frameCount: number;
  idleFrameIndex: number;
  baseFrameDurationMs: number;
}): number {
  const shouldHoldTakeoverReadingFrame =
    shouldFreezeTakeoverReadingPose({
      isTakeoverTopBarSprite,
      movementPhase,
      idleEmote,
      frameCount,
    }) &&
    resolveTopBarDisplayedFrameIndex({
      isTakeoverTopBarSprite,
      movementPhase,
      idleEmote,
      frameCount,
      idleFrameIndex,
    }) === readingMiddleFrameIndex(frameCount);

  return shouldHoldTakeoverReadingFrame ? TAKEOVER_READING_MIDDLE_FRAME_HOLD_MS : baseFrameDurationMs;
}

function pickWeightedRegion(weights: Record<TakeoverRegion, number>): TakeoverRegion {
  const entries = Object.entries(weights) as Array<[TakeoverRegion, number]>;
  const total = entries.reduce((sum, [, weight]) => sum + Math.max(0, weight), 0);
  let cursor = Math.random() * total;
  for (const [region, weight] of entries) {
    cursor -= Math.max(0, weight);
    if (cursor <= 0) {
      return region;
    }
  }
  return "center";
}

function shouldMove(currentRegion: TakeoverRegion, isTakeover: boolean): boolean {
  let threshold = isTakeover ? 0.5 : 0.4;
  if (currentRegion !== "center") {
    threshold += 0.12;
  }
  return Math.random() < threshold;
}

function pickNextRegion(
  currentRegion: TakeoverRegion,
  previousRegion: TakeoverRegion | null,
  isTakeover: boolean,
  sessionState: NonNullable<SessionStoreState["session"]>["state"] | "idle",
): TakeoverRegion {
  const weights: Record<TakeoverRegion, number> = {
    logo: 1.05,
    center: 4.6,
    status: 1.15,
  };

  if (currentRegion === "center") {
    weights.center = 2.9;
  } else {
    weights.center += 0.9;
    weights[currentRegion] += 0.35;
  }

  if (isTakeover || sessionState === "takeover" || sessionState === "waiting_for_approval") {
    weights.status += 0.3;
  }

  if (
    (previousRegion === "logo" && currentRegion === "status") ||
    (previousRegion === "status" && currentRegion === "logo")
  ) {
    weights[previousRegion] *= 0.25;
  }

  return pickWeightedRegion(weights);
}

function pickPerchedEmote(region: TakeoverRegion, isTakeover: boolean): TopBarEmote {
  if (isTakeover) {
    return "reading";
  }
  if (region === "status") {
    return Math.random() < 0.18 ? "busy" : "idle";
  }
  if (region === "logo") {
    return Math.random() < 0.08 ? "success" : "idle";
  }
  return "idle";
}

export function StatusBar({
  state,
  leftRailCollapsed,
  onToggleLeftRail,
  showActivityToggle = true,
  reviewActionLabel,
  onReviewAction,
  spriteFamily,
  onSpriteFamilyChange,
}: {
  state: SessionStoreState;
  leftRailCollapsed: boolean;
  onToggleLeftRail: () => void;
  showActivityToggle?: boolean;
  reviewActionLabel?: string | null;
  onReviewAction?: (() => void) | null;
  spriteFamily: SpriteFamily;
  onSpriteFamilyChange: (family: SpriteFamily) => void;
}) {
  const sessionState = state.session?.state ?? "idle";
  const interactionMode = state.session?.interaction_mode ?? "watch";
  const statusLabel =
    sessionState === "waiting_for_approval"
      ? "waiting"
      : sessionState === "takeover"
        ? "manual"
        : sessionState === "running" || sessionState === "starting"
          ? "working"
        : sessionState === "completed"
          ? "done"
        : sessionState === "failed"
          ? "error"
          : "watching";
  const spriteSet = useMemo(() => getSpriteSet(spriteFamily), [spriteFamily]);
  const isTakeoverTopBarSprite =
    interactionMode === "takeover" ||
    sessionState === "takeover" ||
    state.activeIntervention?.kind === "manual_control";
  const showTopBarSprite = (sessionState === "idle" || sessionState === "paused" || isTakeoverTopBarSprite) && state.connectionState !== "error";
  // The top-bar mascot stays center-heavy and only shifts perches occasionally,
  // so the motion reads as attention rather than background decoration.
  const [idleFrameIndex, setIdleFrameIndex] = useState(0);
  const [hoverThought, setHoverThought] = useState<string | null>(null);
  const [currentRegion, setCurrentRegion] = useState<TakeoverRegion>("center");
  const [previousRegion, setPreviousRegion] = useState<TakeoverRegion | null>(null);
  const [currentLane, setCurrentLane] = useState(50);
  const [movementPhase, setMovementPhase] = useState<MovementPhase>("perched");
  const [pendingMove, setPendingMove] = useState<{
    region: TakeoverRegion;
    lane: number;
    durationMs: number;
  } | null>(null);
  const [perchedEmote, setPerchedEmote] = useState<TopBarEmote>("idle");
  const [movementCycle, setMovementCycle] = useState(0);
  const hasShownInitialTakeoverThoughtRef = useRef(false);
  const takeoverThoughtClearTimeoutRef = useRef<number | null>(null);
  // Hover copy stays short on purpose so it reads like a tiny ambient thought,
  // not a second UI panel competing with the stage.
  const hoverThoughts = useMemo(
    () =>
      spriteFamily === "dog"
        ? [
            "👋",
            "still watching.",
            "good dog. good oversight.",
            "no weird clicks.",
            "that button looked suspicious.",
            "i saw that.",
            "maybe not that tab.",
            "subtle. very subtle.",
            "carry on.",
            "this better not be prod.",
            "great. another modal.",
            "bold move.",
            "i'm logging this mentally.",
            "that felt unnecessary.",
            "manual review energy.",
            "please let this be the right button.",
          ]
        : [
            "👋",
            "still watching.",
            "no weird clicks.",
            "tiny lobster, big oversight.",
            "all clear.",
            "that button looked suspicious.",
            "i saw that.",
            "maybe not that tab.",
            "subtle. very subtle.",
            "absolutely intentional, i hope.",
            "carry on.",
            "this better not be prod.",
            "great. another modal.",
            "this workflow has layers.",
            "bold move.",
            "i'm logging this mentally.",
            "that felt unnecessary.",
            "we're really doing this.",
            "interesting definition of safe.",
            "not my first sketchy redirect.",
            "clean click. questionable intent.",
            "i respect the confidence.",
            "one more popup and i'm filing a complaint.",
            "this could have been a shortcut.",
            "please let this be the right button.",
            "i've seen worse. barely.",
            "the audit trail writes itself.",
            "ah yes, the scenic route.",
            "we're flirting with a bad idea.",
            "impeccable chaos.",
            "if this opens a login wall, i'm judging.",
            "manual review energy.",
          ],
    [spriteFamily],
  );
  const takeoverThoughts = useMemo(
    () =>
      ({
        logo: [
          "i swear i look like a certain claw.",
          "isn't lumon in a certain tv show...",
          "great name. no notes. several concerns.",
          "this feels branded in a way that should worry people.",
          "unfortunate place to become self-aware.",
        ],
        center: [
          "you know i can do this better right?",
          "if you don't need me why am i here?",
          "i'm posting this on molthub.",
          "rentahuman might definitely need you.",
          "not to be dramatic but this is how tickets get born.",
        ],
        status: [
          "love when the interface quietly confirms things have deteriorated.",
          "good. a small label for a large problem.",
          "the status is concise. the situation is not.",
          "helpful that the UI is documenting the decline.",
          "very brave of the badge to say that out loud.",
        ],
      }) satisfies Record<TakeoverRegion, string[]>,
    [spriteFamily],
  );

  const activeRegion = movementPhase === "moving" && pendingMove ? pendingMove.region : currentRegion;
  const activeLane = movementPhase === "moving" && pendingMove ? pendingMove.lane : currentLane;
  const activeEmote = movementPhase === "moving" ? "locomotion" : isTakeoverTopBarSprite ? "reading" : perchedEmote;
  const activeThoughts = isTakeoverTopBarSprite ? takeoverThoughts[activeRegion] : hoverThoughts;
  const guaranteedTakeoverThought =
    takeoverThoughts.center[3] ?? takeoverThoughts.center[0] ?? "manual review energy.";
  const pickThought = (thoughts: string[]) => thoughts[Math.floor(Math.random() * thoughts.length)] ?? "👋";
  const clearTakeoverThoughtTimeout = () => {
    if (takeoverThoughtClearTimeoutRef.current === null) {
      return;
    }

    window.clearTimeout(takeoverThoughtClearTimeoutRef.current);
    takeoverThoughtClearTimeoutRef.current = null;
  };

  const idleEmote = activeEmote;
  const idleAnimation = spriteSet.manifest.animations[idleEmote];
  const idleFrameDurationMs =
    idleAnimation.frame_duration_ms *
    (idleEmote === "idle"
      ? 1.85
      : idleEmote === "busy"
        ? 0.9
        : idleEmote === "reading"
          ? 1.7
          : idleEmote === "locomotion"
            ? 1.55
            : 1.05);
  const displayedFrameIndex = resolveTopBarDisplayedFrameIndex({
    isTakeoverTopBarSprite,
    movementPhase,
    idleEmote,
    frameCount: idleAnimation.frame_paths.length,
    idleFrameIndex,
  });
  const idleFramePath = idleAnimation.frame_paths[displayedFrameIndex];
  const isHeldTakeoverReadingPose =
    displayedFrameIndex === readingMiddleFrameIndex(idleAnimation.frame_paths.length) &&
    shouldFreezeTakeoverReadingPose({
      isTakeoverTopBarSprite,
      movementPhase,
      idleEmote,
      frameCount: idleAnimation.frame_paths.length,
    });

  useEffect(() => {
    if (!showTopBarSprite) {
      setIdleFrameIndex(0);
      setHoverThought(null);
      setCurrentRegion("center");
      setPreviousRegion(null);
      setCurrentLane(50);
      setMovementPhase("perched");
      setPendingMove(null);
      setPerchedEmote("idle");
      setMovementCycle(0);
      return;
    }

    // Step through the source sprite frames for the current emote while the
    // mascot is perched in the top bar.
    const frameTimeout = window.setTimeout(() => {
      setIdleFrameIndex((value) => (value + 1) % idleAnimation.frame_paths.length);
    },
    resolveTopBarIdleFrameDelayMs({
      isTakeoverTopBarSprite,
      movementPhase,
      idleEmote,
      frameCount: idleAnimation.frame_paths.length,
      idleFrameIndex,
      baseFrameDurationMs: idleFrameDurationMs,
    }));

    return () => window.clearTimeout(frameTimeout);
  }, [idleAnimation.frame_paths.length, idleEmote, idleFrameDurationMs, idleFrameIndex, isTakeoverTopBarSprite, movementPhase, showTopBarSprite]);

  useEffect(() => {
    if (!showTopBarSprite) {
      return;
    }

    if (movementPhase === "perched") {
      const dwellTimeout = window.setTimeout(() => {
        if (!shouldMove(currentRegion, isTakeoverTopBarSprite)) {
          setPerchedEmote(pickPerchedEmote(currentRegion, isTakeoverTopBarSprite));
          setMovementCycle((value) => value + 1);
          return;
        }

        const nextRegion = pickNextRegion(currentRegion, previousRegion, isTakeoverTopBarSprite, sessionState);
        const nextLane = randomLane(nextRegion);
        setPendingMove({
          region: nextRegion,
          lane: nextLane,
          durationMs: moveDurationMs(currentLane, nextLane),
        });
        setHoverThought(null);
        setIdleFrameIndex(0);
        setMovementPhase("considering_move");
      }, dwellDurationMs(currentRegion, isTakeoverTopBarSprite));

      return () => window.clearTimeout(dwellTimeout);
    }

    if (movementPhase === "considering_move") {
      const considerTimeout = window.setTimeout(() => {
        if (!pendingMove) {
          setMovementPhase("perched");
          return;
        }
        setIdleFrameIndex(0);
        setMovementPhase("moving");
      }, considerDurationMs());

      return () => window.clearTimeout(considerTimeout);
    }

    if (movementPhase === "moving") {
      const moveTimeout = window.setTimeout(() => {
        if (!pendingMove) {
          setMovementPhase("perched");
          return;
        }
        setPreviousRegion(currentRegion);
        setCurrentRegion(pendingMove.region);
        setCurrentLane(pendingMove.lane);
        setPerchedEmote(pickPerchedEmote(pendingMove.region, isTakeoverTopBarSprite));
        setPendingMove(null);
        setIdleFrameIndex(0);
        setMovementPhase("settling");
      }, pendingMove?.durationMs ?? 0);

      return () => window.clearTimeout(moveTimeout);
    }

    const settleTimeout = window.setTimeout(() => {
      setIdleFrameIndex(0);
      setMovementPhase("perched");
    }, settleDurationMs());

    return () => window.clearTimeout(settleTimeout);
  }, [
    currentLane,
    currentRegion,
    isTakeoverTopBarSprite,
    movementCycle,
    movementPhase,
    pendingMove,
    previousRegion,
    sessionState,
    showTopBarSprite,
  ]);

  useEffect(() => {
    setHoverThought(null);
    setIdleFrameIndex(0);
    setPerchedEmote(pickPerchedEmote(currentRegion, isTakeoverTopBarSprite));
    setMovementPhase("settling");
    setPendingMove(null);
  }, [currentRegion, isTakeoverTopBarSprite]);

  useEffect(() => {
    if (showTopBarSprite && isTakeoverTopBarSprite) {
      return;
    }

    clearTakeoverThoughtTimeout();
    hasShownInitialTakeoverThoughtRef.current = false;
  }, [isTakeoverTopBarSprite, showTopBarSprite]);

  useEffect(() => () => {
    clearTakeoverThoughtTimeout();
  }, []);

  useEffect(() => {
    if (!showTopBarSprite || !isTakeoverTopBarSprite || movementPhase !== "perched") {
      return;
    }

    if (!hasShownInitialTakeoverThoughtRef.current) {
      return;
    }

    if (takeoverThoughtClearTimeoutRef.current !== null) {
      return;
    }

    if (Math.random() >= 0.32) {
      return;
    }

    const regionThoughts = takeoverThoughts[currentRegion];
    const autoThoughtDelay = thoughtDelayMs();
    const showThoughtTimeout = window.setTimeout(() => {
      setHoverThought(pickThought(regionThoughts));
    }, autoThoughtDelay);
    const clearThoughtTimeout = window.setTimeout(() => {
      setHoverThought((current) => (regionThoughts.includes(current ?? "") ? null : current));
    }, autoThoughtDelay + TAKEOVER_THOUGHT_DURATION_MS);

    return () => {
      window.clearTimeout(showThoughtTimeout);
      window.clearTimeout(clearThoughtTimeout);
    };
  }, [currentRegion, isTakeoverTopBarSprite, movementPhase, showTopBarSprite, takeoverThoughts]);

  useEffect(() => {
    if (
      !showTopBarSprite ||
      !isTakeoverTopBarSprite ||
      movementPhase !== "perched" ||
      hasShownInitialTakeoverThoughtRef.current
    ) {
      return;
    }

    hasShownInitialTakeoverThoughtRef.current = true;
    clearTakeoverThoughtTimeout();
    setHoverThought(guaranteedTakeoverThought);

    takeoverThoughtClearTimeoutRef.current = window.setTimeout(() => {
      setHoverThought((current) =>
        current === guaranteedTakeoverThought ? null : current,
      );
      takeoverThoughtClearTimeoutRef.current = null;
    }, TAKEOVER_INITIAL_THOUGHT_DURATION_MS);
  }, [
    guaranteedTakeoverThought,
    isTakeoverTopBarSprite,
    movementPhase,
    showTopBarSprite,
  ]);

  const spriteStyle = {
    left: formatLane(activeLane),
    transitionDuration: movementPhase === "moving" && pendingMove ? `${pendingMove.durationMs}ms` : undefined,
  };
  const thoughtStyle = {
    left: formatLane(activeLane),
    transitionDuration: movementPhase === "moving" && pendingMove ? `${pendingMove.durationMs}ms` : undefined,
  };

  return (
    <header className="status-bar">
      {showTopBarSprite ? (
        <div className="status-idle-layer" aria-hidden="true">
          {hoverThought ? (
            <div className="status-thought-bubble" style={thoughtStyle}>
              {hoverThought}
            </div>
          ) : null}
          <img
            className={`status-idle-sprite status-idle-sprite-${idleEmote}${isHeldTakeoverReadingPose ? " status-idle-sprite-is-held" : ""}`}
            style={spriteStyle}
            src={`${spriteSet.assetBasePath}/${idleFramePath}`}
            alt=""
            data-status-sprite-mode={isTakeoverTopBarSprite ? "takeover" : "ambient"}
            data-status-sprite-emote={idleEmote}
            data-status-sprite-frame={displayedFrameIndex}
            data-status-stop-region={activeRegion}
            onMouseEnter={() => {
              setHoverThought(pickThought(activeThoughts));
            }}
            onMouseLeave={() => setHoverThought(null)}
          />
        </div>
      ) : null}
      <div className="status-brand">
        <strong>Lumon</strong>
      </div>
      <div className="status-meta">
        <span className={`connection-indicator connection-${state.connectionState} status-${statusLabel}`}>
          <span className="connection-dot" />
          <span className="connection-label">{statusLabel}</span>
        </span>
        {reviewActionLabel && onReviewAction ? (
          <button type="button" className="status-inline-action" onClick={onReviewAction}>
            {reviewActionLabel}
          </button>
        ) : null}
        <div className="rail-toggle-group" aria-label="Observation drawers">
          <div className="sprite-family-picker-container">
            <span className="sprite-picker-label">Mascot:</span>
            <div className="sprite-family-picker" role="group" aria-label="Mascot">
              {SPRITE_FAMILY_OPTIONS.map((option) => (
                <button
                  key={option.family}
                  type="button"
                  className={`sprite-family-button${spriteFamily === option.family ? " is-active" : ""}`}
                  onClick={() => onSpriteFamilyChange(option.family)}
                  aria-pressed={spriteFamily === option.family}
                  title={`Use ${option.label.toLowerCase()} mascot`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          {showActivityToggle ? (
            <button
              type="button"
              className={`rail-toggle ${leftRailCollapsed ? "" : "is-active"}`}
              onClick={onToggleLeftRail}
              aria-pressed={!leftRailCollapsed}
              aria-label={leftRailCollapsed ? "Show activity" : "Hide activity"}
              title={leftRailCollapsed ? "Show activity" : "Hide activity"}
            >
              ⏱
            </button>
          ) : null}
        </div>
      </div>
    </header>
  );
}
