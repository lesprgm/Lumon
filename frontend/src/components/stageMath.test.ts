import { describe, expect, it } from "vitest";

import { scaleRect, scaleX, scaleY } from "./stageMath";

describe("stageMath", () => {
  it("scales points from 1920x1080 base coordinates", () => {
    expect(scaleX(960, 640)).toBe(320);
    expect(scaleY(540, 400)).toBe(200);
  });

  it("scales rectangles proportionally", () => {
    expect(scaleRect({ x: 480, y: 270, width: 192, height: 108 }, 640, 400)).toEqual({
      x: 160,
      y: 100,
      width: 64,
      height: 40,
    });
  });
});
