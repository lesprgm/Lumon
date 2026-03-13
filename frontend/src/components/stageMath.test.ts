import { describe, expect, it } from "vitest";

import { scaleRect, scaleX, scaleY } from "./stageMath";

describe("stageMath", () => {
  it("scales points from 1280x800 base coordinates", () => {
    expect(scaleX(640, 640)).toBe(320);
    expect(scaleY(400, 400)).toBe(200);
  });

  it("scales rectangles proportionally", () => {
    expect(scaleRect({ x: 320, y: 200, width: 128, height: 80 }, 640, 400)).toEqual({
      x: 160,
      y: 100,
      width: 64,
      height: 40,
    });
  });
});
