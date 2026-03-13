import { afterEach, describe, expect, it, vi } from "vitest";

import { readStoredSpriteFamily, writeStoredSpriteFamily } from "./spriteSelection";

describe("spriteSelection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to lobster when nothing is stored", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => null,
        setItem: () => undefined,
      },
    });
    expect(readStoredSpriteFamily()).toBe("lobster");
  });

  it("persists and restores the selected sprite family", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => {
          store.set(key, value);
        },
      },
    });
    writeStoredSpriteFamily("dog");
    expect(readStoredSpriteFamily()).toBe("dog");
  });
});
