import type {
  LumonSpriteAnimationId,
  SpriteRuntimeManifest,
} from "./types";

export type SpriteFrameCache = Record<LumonSpriteAnimationId, HTMLImageElement[]>;

function trimTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

export function resolveSpriteAssetPath(
  relativePath: string,
  assetBasePath = "",
  assetRoot = ".",
): string {
  const segments = [assetBasePath, assetRoot, relativePath]
    .filter((segment) => segment.length > 0 && segment !== ".")
    .map(trimTrailingSlash);

  if (segments.length === 0) {
    return relativePath;
  }

  return segments.join("/");
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Failed to load sprite frame: ${src}`));
    image.src = src;
  });
}

export async function preloadSpriteFrames(
  manifest: SpriteRuntimeManifest,
  assetBasePath = "",
): Promise<SpriteFrameCache> {
  const entries = await Promise.all(
    Object.entries(manifest.animations).map(async ([animationId, animation]) => {
      const frames = await Promise.all(
        animation.frame_paths.map((relativePath) =>
          loadImage(resolveSpriteAssetPath(relativePath, assetBasePath, manifest.asset_root)),
        ),
      );
      return [animationId, frames] as const;
    }),
  );

  return Object.fromEntries(entries) as SpriteFrameCache;
}

