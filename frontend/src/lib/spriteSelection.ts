import {
  DEFAULT_SPRITE_FAMILY,
  normalizeSpriteFamily,
  type SpriteFamily,
} from "../overlay/sprites";

const STORAGE_KEY = "lumon.sprite_family";

export function readStoredSpriteFamily(): SpriteFamily {
  if (typeof window === "undefined") {
    return DEFAULT_SPRITE_FAMILY;
  }
  return normalizeSpriteFamily(window.localStorage.getItem(STORAGE_KEY));
}

export function writeStoredSpriteFamily(family: SpriteFamily): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, family);
}
