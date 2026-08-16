export function resolveSpriteAssetPath(
  relativePath: string,
  assetBasePath = "",
): string {
  return assetBasePath
    ? `${assetBasePath.replace(/\/$/, "")}/${relativePath}`
    : relativePath;
}
