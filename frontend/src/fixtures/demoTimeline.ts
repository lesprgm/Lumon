import happyPathTimeline from "../../../backend/app/fixtures/timelines/happy_path.json";

import type { AnyServerEnvelope } from "../protocol/types";

type RawTimedMessage = {
  delay_ms: number;
  message: AnyServerEnvelope;
};

export type TimedMessage = {
  delayMs: number;
  message: AnyServerEnvelope;
};

export const demoTimeline: TimedMessage[] = (happyPathTimeline as RawTimedMessage[]).map((entry) => ({
  delayMs: entry.delay_ms,
  message: entry.message,
}));
