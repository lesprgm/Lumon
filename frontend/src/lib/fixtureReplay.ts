import type { AnyServerEnvelope } from "../protocol/types";
import type { TimedMessage } from "../fixtures/demoTimeline";

const REPLAY_SPEED_MULTIPLIER = 1.8;

export function startFixtureReplay(
  messages: TimedMessage[],
  onMessage: (message: AnyServerEnvelope) => void,
): () => void {
  const timers: Array<ReturnType<typeof globalThis.setTimeout>> = [];
  let elapsed = 0;
  for (const entry of messages) {
    elapsed += Math.round(entry.delayMs * REPLAY_SPEED_MULTIPLIER);
    const timer = globalThis.setTimeout(() => onMessage(entry.message), elapsed);
    timers.push(timer);
  }
  return () => {
    for (const timer of timers) {
      globalThis.clearTimeout(timer);
    }
  };
}
