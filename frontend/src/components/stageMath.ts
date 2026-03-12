const BASE_WIDTH = 1280;
const BASE_HEIGHT = 800;

export function scaleX(x: number, width: number): number {
  return (x / BASE_WIDTH) * width;
}

export function scaleY(y: number, height: number): number {
  return (y / BASE_HEIGHT) * height;
}

export function unscaleX(x: number, width: number): number {
  return (x / width) * BASE_WIDTH;
}

export function unscaleY(y: number, height: number): number {
  return (y / height) * BASE_HEIGHT;
}

export function scaleRect(
  rect: { x: number; y: number; width: number; height: number },
  width: number,
  height: number,
): { x: number; y: number; width: number; height: number } {
  return {
    x: scaleX(rect.x, width),
    y: scaleY(rect.y, height),
    width: scaleX(rect.width, width),
    height: scaleY(rect.height, height),
  };
}
