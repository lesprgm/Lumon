import { dogRuntimeManifest } from "./dogRuntimeManifest";
import { lobsterRuntimeManifest } from "./lobsterRuntimeManifest";
import type { SpriteRuntimeManifest } from "./types";

export type SpriteFamily = "lobster" | "dog";

export interface SpriteSet {
  family: SpriteFamily;
  label: string;
  manifest: SpriteRuntimeManifest;
  assetBasePath: string;
}

export const DEFAULT_SPRITE_FAMILY: SpriteFamily = "lobster";

const SPRITE_SETS: Record<SpriteFamily, SpriteSet> = {
  lobster: {
    family: "lobster",
    label: "Lobster",
    manifest: lobsterRuntimeManifest,
    assetBasePath: "/assets/lobster",
  },
  dog: {
    family: "dog",
    label: "Dog",
    manifest: dogRuntimeManifest,
    assetBasePath: "/assets/dog",
  },
};

export const SPRITE_FAMILY_OPTIONS = [SPRITE_SETS.lobster, SPRITE_SETS.dog] as const;

export function normalizeSpriteFamily(value: string | null | undefined): SpriteFamily {
  if (value === "dog") {
    return "dog";
  }
  return DEFAULT_SPRITE_FAMILY;
}

export function getSpriteSet(family: SpriteFamily): SpriteSet {
  return SPRITE_SETS[family];
}
