from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPRITE_ROOT = ROOT / "dog sprite"
OUTPUT_ROOT = SPRITE_ROOT / "cleaned"
PUBLIC_ROOT = ROOT / "frontend" / "public" / "assets" / "dog"
STATE_ORDER = ("idle", "busy", "success", "error", "locomotion")
STATE_GRID = {
    "idle": (6, 1),
    "busy": (3, 2),
    "success": (3, 3),
    "error": (3, 2),
    "locomotion": (2, 3),
}
STATE_PLAYBACK = {
    "idle": {"fps": 8, "loop": True, "hold_last_frame_ms": 0},
    "busy": {"fps": 10, "loop": True, "hold_last_frame_ms": 0},
    "success": {"fps": 8, "loop": False, "hold_last_frame_ms": 450},
    "error": {"fps": 8, "loop": False, "hold_last_frame_ms": 650},
    "locomotion": {"fps": 12, "loop": True, "hold_last_frame_ms": 0},
}


@dataclass
class FrameBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass
class Component:
    pixels: list[tuple[int, int]]
    area: int
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def latest_state_files() -> dict[str, Path]:
    state_files: dict[str, Path] = {}
    for state in STATE_ORDER:
        candidates = sorted((SPRITE_ROOT / state).glob("seedream_*.png"))
        if not candidates:
            raise RuntimeError(f"No generated sheet found for state: {state}")
        state_files[state] = candidates[-1]
    return state_files


def quantize_colors(colors: list[tuple[int, int, int]], quant: int = 8, top_n: int = 24) -> np.ndarray:
    quantized = [tuple((channel // quant) * quant for channel in rgb) for rgb in colors]
    palette = [color for color, _ in Counter(quantized).most_common(top_n)]
    return np.array(palette, dtype=np.int16)


def border_palette(arr: np.ndarray, step: int = 4) -> np.ndarray:
    height, width = arr.shape[:2]
    colors: list[tuple[int, int, int]] = []
    for x in range(0, width, step):
        colors.append(tuple(arr[0, x, :3]))
        colors.append(tuple(arr[height - 1, x, :3]))
    for y in range(0, height, step):
        colors.append(tuple(arr[y, 0, :3]))
        colors.append(tuple(arr[y, width - 1, :3]))
    return quantize_colors(colors)


def corner_palette(arr: np.ndarray, patch: int = 24, step: int = 2) -> np.ndarray:
    height, width = arr.shape[:2]
    patch_h = min(patch, height)
    patch_w = min(patch, width)
    corners = [
        arr[:patch_h, :patch_w, :3],
        arr[:patch_h, width - patch_w :, :3],
        arr[height - patch_h :, :patch_w, :3],
        arr[height - patch_h :, width - patch_w :, :3],
    ]
    colors: list[tuple[int, int, int]] = []
    for corner in corners:
        for y in range(0, corner.shape[0], step):
            for x in range(0, corner.shape[1], step):
                colors.append(tuple(int(v) for v in corner[y, x]))
    return quantize_colors(colors, top_n=12)


def has_green_screen_background(arr: np.ndarray) -> bool:
    rgb = arr[:, :, :3].astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    greenish = (green >= 170) & ((green - red) >= 18) & ((green - blue) >= 105)
    return float(greenish.mean()) >= 0.18


def alpha_mask_from_green_screen(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    background = (green >= 180) & ((green - red) >= 18) & ((green - blue) >= 105)
    return np.where(background, 0, 255).astype(np.uint8)


def defringe_green_screen(arr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    opaque = alpha > 0
    transparent = ~opaque
    touches_transparent = np.zeros_like(opaque, dtype=bool)
    touches_transparent[1:, :] |= transparent[:-1, :]
    touches_transparent[:-1, :] |= transparent[1:, :]
    touches_transparent[:, 1:] |= transparent[:, :-1]
    touches_transparent[:, :-1] |= transparent[:, 1:]
    green_spill = opaque & touches_transparent & ((green - red) >= 10) & ((green - blue) >= 55)
    cleaned = alpha.copy()
    cleaned[green_spill] = 0
    return cleaned


def despill_green_edges(arr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    result = arr.copy()
    rgb = result[:, :, :3].astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    opaque = alpha > 0
    transparent = ~opaque
    touches_transparent = np.zeros_like(opaque, dtype=bool)
    touches_transparent[1:, :] |= transparent[:-1, :]
    touches_transparent[:-1, :] |= transparent[1:, :]
    touches_transparent[:, 1:] |= transparent[:, :-1]
    touches_transparent[:, :-1] |= transparent[:, 1:]
    spill = opaque & touches_transparent & ((green - red) >= 4) & ((green - blue) >= 18)
    if not spill.any():
        return result

    new_green = np.minimum(green, red + 10)
    new_blue = np.maximum(blue, red - 22)
    result[:, :, 1] = np.where(spill, new_green, green).astype(np.uint8)
    result[:, :, 2] = np.where(spill, new_blue, blue).astype(np.uint8)
    return result


def background_candidates(arr: np.ndarray, palette: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    brightest = rgb.max(axis=2)
    channel_spread = rgb.max(axis=2) - rgb.min(axis=2)
    distances = ((rgb[:, :, None, :] - palette[None, None, :, :]) ** 2).sum(axis=3)
    min_distance = distances.min(axis=2)
    return (brightest >= 132) & (channel_spread <= 48) & (min_distance <= 52**2)


def flood_fill_background(candidates: np.ndarray) -> np.ndarray:
    height, width = candidates.shape
    background = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(y: int, x: int) -> None:
        if candidates[y, x] and not background[y, x]:
            background[y, x] = True
            queue.append((y, x))

    for x in range(width):
        enqueue(0, x)
        enqueue(height - 1, x)
    for y in range(height):
        enqueue(y, 0)
        enqueue(y, width - 1)

    while queue:
        y, x = queue.popleft()
        if y > 0:
            enqueue(y - 1, x)
        if y + 1 < height:
            enqueue(y + 1, x)
        if x > 0:
            enqueue(y, x - 1)
        if x + 1 < width:
            enqueue(y, x + 1)

    return background


def alpha_mask_from_background(arr: np.ndarray, *, corner_locked: bool = False) -> np.ndarray:
    palette = corner_palette(arr) if corner_locked else border_palette(arr)
    background = flood_fill_background(background_candidates(arr, palette))
    alpha = np.where(background, 0, 255).astype(np.uint8)
    return alpha


def defringe(arr: np.ndarray, alpha: np.ndarray, *, corner_locked: bool = False) -> np.ndarray:
    palette = corner_palette(arr) if corner_locked else border_palette(arr)
    rgb = arr[:, :, :3].astype(np.int16)
    channel_spread = rgb.max(axis=2) - rgb.min(axis=2)
    distances = ((rgb[:, :, None, :] - palette[None, None, :, :]) ** 2).sum(axis=3)
    min_distance = distances.min(axis=2)

    opaque = alpha > 0
    transparent = ~opaque
    touches_transparent = np.zeros_like(opaque, dtype=bool)
    touches_transparent[1:, :] |= transparent[:-1, :]
    touches_transparent[:-1, :] |= transparent[1:, :]
    touches_transparent[:, 1:] |= transparent[:, :-1]
    touches_transparent[:, :-1] |= transparent[:, 1:]

    fringe = opaque & touches_transparent & (channel_spread <= 56) & (min_distance <= 72**2)
    cleaned_alpha = alpha.copy()
    cleaned_alpha[fringe] = 0
    return cleaned_alpha


def connected_components(mask: np.ndarray) -> list[Component]:
    height, width = mask.shape
    seen = np.zeros((height, width), dtype=bool)
    components: list[Component] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(y, x)])
            seen[y, x] = True
            pixels: list[tuple[int, int]] = []
            min_x = max_x = x
            min_y = max_y = y
            while queue:
                cy, cx = queue.popleft()
                pixels.append((cy, cx))
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if ny < 0 or ny >= height or nx < 0 or nx >= width:
                        continue
                    if seen[ny, nx] or not mask[ny, nx]:
                        continue
                    seen[ny, nx] = True
                    queue.append((ny, nx))
            components.append(
                Component(
                    pixels=pixels,
                    area=len(pixels),
                    x0=min_x,
                    y0=min_y,
                    x1=max_x + 1,
                    y1=max_y + 1,
                )
            )
    return components


def expand_bbox(component: Component, margin: int, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        max(component.x0 - margin, 0),
        max(component.y0 - margin, 0),
        min(component.x1 + margin, width),
        min(component.y1 + margin, height),
    )


def component_intersects_bbox(component: Component, bbox: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = bbox
    return not (component.x1 <= x0 or component.x0 >= x1 or component.y1 <= y0 or component.y0 >= y1)


def isolate_subject(mask: np.ndarray) -> np.ndarray:
    components = connected_components(mask)
    if not components:
        return np.zeros_like(mask, dtype=bool)

    primary = max(components, key=lambda item: item.area)
    height, width = mask.shape
    primary_bbox = expand_bbox(primary, margin=8, width=width, height=height)
    kept = np.zeros_like(mask, dtype=bool)

    for component in components:
        near_primary = component_intersects_bbox(component, primary_bbox)
        large_relative = component.area >= max(16, int(primary.area * 0.08))
        expressive_mark = component.area >= 6 and near_primary
        floor_strip = (
            component is not primary
            and component.height <= max(10, int(height * 0.07))
            and component.width >= int(width * 0.28)
            and component.y0 >= int(height * 0.7)
        )
        if floor_strip:
            continue
        if component is primary or (near_primary and (large_relative or expressive_mark)):
            for py, px in component.pixels:
                kept[py, px] = True

    return kept


def normalize_frame(frame: Image.Image, canvas_size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - frame.width) // 2
    y = canvas_size[1] - frame.height
    canvas.alpha_composite(frame, (x, y))
    return canvas


def save_sheet(frames: list[Image.Image], output_path: Path) -> None:
    if not frames:
        return
    width = sum(frame.width for frame in frames)
    height = max(frame.height for frame in frames)
    sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cursor = 0
    for frame in frames:
        sheet.alpha_composite(frame, (cursor, 0))
        cursor += frame.width
    sheet.save(output_path)


def clear_png_outputs(directory: Path) -> None:
    for path in directory.glob("*.png"):
        path.unlink()


def build_runtime_manifest(manifest: dict[str, object]) -> dict[str, object]:
    canvas = manifest["canvas_size"]
    canvas_width = int(canvas["width"])
    canvas_height = int(canvas["height"])
    anchor = {
        "kind": "bottom_center",
        "normalized_x": 0.5,
        "normalized_y": 1.0,
        "pixel_x": canvas_width // 2,
        "pixel_y": canvas_height - 1,
    }

    animations: dict[str, object] = {}
    for state, state_data in manifest["states"].items():
        frame_count = int(state_data["frame_count"])
        playback = STATE_PLAYBACK[state]
        animations[state] = {
            "sheet_path": f"{state}/{state}_sheet_normalized.png",
            "frames_dir": f"{state}/frames",
            "frame_paths": [f"{state}/frames/{state}_{index:02d}.png" for index in range(1, frame_count + 1)],
            "frame_count": frame_count,
            "fps": playback["fps"],
            "frame_duration_ms": round(1000 / playback["fps"]),
            "loop": playback["loop"],
            "hold_last_frame_ms": playback["hold_last_frame_ms"],
        }

    return {
        "sprite_id": "lumon_dog_v1",
        "version": 1,
        "asset_root": ".",
        "logical_canvas": canvas,
        "default_anchor": anchor,
        "default_animation": "idle",
        "animations": animations,
        "runtime_state_map": {
            "session_state_to_animation": {
                "idle": "idle",
                "starting": "idle",
                "running": "idle",
                "pause_requested": "idle",
                "paused": "idle",
                "waiting_for_approval": "idle",
                "takeover": "idle",
                "completed": "success",
                "failed": "error",
                "stopped": "idle",
            },
            "action_type_to_animation": {
                "navigate": "busy",
                "click": "busy",
                "type": "busy",
                "scroll": "busy",
                "read": "busy",
                "wait": "idle",
                "complete": "success",
                "error": "error",
            },
            "priority": ["error", "success", "busy", "locomotion", "idle"],
            "default": "idle",
        },
        "recommended_transitions": {
            "on_success_complete": {"next_animation": "idle", "delay_ms": 450},
            "on_error_complete": {"next_animation": "idle", "delay_ms": 650},
        },
    }


def main() -> None:
    state_files = latest_state_files()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)

    extracted: dict[str, dict[str, object]] = {}
    global_max_width = 0
    global_max_height = 0

    for state, path in state_files.items():
        image = Image.open(path).convert("RGBA")
        arr = np.array(image)
        cleaned = arr.copy()
        cleaned[:, :, 3] = 0
        cropped_frames: list[Image.Image] = []
        manifest_boxes: list[dict[str, int]] = []
        height, width = arr.shape[:2]
        cols, rows = STATE_GRID[state]
        cell_width = width / cols
        cell_height = height / rows

        for row_index in range(rows):
            for col_index in range(cols):
                cell_x0 = int(round(col_index * cell_width))
                cell_x1 = int(round((col_index + 1) * cell_width))
                cell_y0 = int(round(row_index * cell_height))
                cell_y1 = int(round((row_index + 1) * cell_height))
                cell_arr = arr[cell_y0:cell_y1, cell_x0:cell_x1].copy()
                if has_green_screen_background(cell_arr):
                    cell_alpha = alpha_mask_from_green_screen(cell_arr)
                    cell_alpha = defringe_green_screen(cell_arr, cell_alpha)
                else:
                    cell_alpha = alpha_mask_from_background(cell_arr, corner_locked=True)
                    cell_alpha = defringe(cell_arr, cell_alpha, corner_locked=True)
                region_alpha = cell_alpha > 0
                isolated = isolate_subject(region_alpha)
                if isolated.sum() < 800:
                    continue

                cleaned[cell_y0:cell_y1, cell_x0:cell_x1, 3] = np.where(isolated, 255, 0).astype(np.uint8)
                inner_rows = isolated.any(axis=1)
                inner_cols = isolated.any(axis=0)
                inner_y = np.flatnonzero(inner_rows)
                inner_x = np.flatnonzero(inner_cols)
                box = FrameBox(
                    x0=cell_x0 + int(inner_x[0]),
                    y0=cell_y0 + int(inner_y[0]),
                    x1=cell_x0 + int(inner_x[-1]) + 1,
                    y1=cell_y0 + int(inner_y[-1]) + 1,
                )
                frame_arr = arr[box.y0:box.y1, box.x0:box.x1].copy()
                frame_alpha = cleaned[box.y0:box.y1, box.x0:box.x1, 3]
                frame_arr[:, :, 3] = frame_alpha
                if has_green_screen_background(cell_arr):
                    frame_arr = despill_green_edges(frame_arr, frame_alpha)
                frame = Image.fromarray(frame_arr)
                cropped_frames.append(frame)
                manifest_boxes.append(
                    {
                        "x0": box.x0,
                        "y0": box.y0,
                        "x1": box.x1,
                        "y1": box.y1,
                        "width": box.width,
                        "height": box.height,
                    }
                )
                global_max_width = max(global_max_width, frame.width)
                global_max_height = max(global_max_height, frame.height)

        cleaned_image = Image.fromarray(cleaned)

        extracted[state] = {
            "cleaned_image": cleaned_image,
            "cropped_frames": cropped_frames,
            "boxes": manifest_boxes,
            "source": str(path.relative_to(ROOT)),
            "final_frames": list(cropped_frames),
        }

    canvas_size = (global_max_width + 32, global_max_height + 32)
    manifest: dict[str, object] = {"canvas_size": {"width": canvas_size[0], "height": canvas_size[1]}, "states": {}}

    for state, data in extracted.items():
        state_dir = OUTPUT_ROOT / state
        public_state_dir = PUBLIC_ROOT / state
        frames_dir = state_dir / "frames"
        public_frames_dir = public_state_dir / "frames"
        cropped_dir = state_dir / "frames_cropped"
        state_dir.mkdir(parents=True, exist_ok=True)
        public_state_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        public_frames_dir.mkdir(parents=True, exist_ok=True)
        cropped_dir.mkdir(parents=True, exist_ok=True)
        clear_png_outputs(frames_dir)
        clear_png_outputs(public_frames_dir)
        clear_png_outputs(cropped_dir)

        cleaned_path = state_dir / f"{state}_sheet_transparent.png"
        public_cleaned_path = public_state_dir / f"{state}_sheet_transparent.png"
        data["cleaned_image"].save(cleaned_path)
        data["cleaned_image"].save(public_cleaned_path)

        normalized_frames: list[Image.Image] = []
        cropped_frames: list[Image.Image] = data["cropped_frames"]
        for index, frame in enumerate(cropped_frames, start=1):
            cropped_path = cropped_dir / f"{state}_{index:02d}.png"
            frame.save(cropped_path)

            normalized = normalize_frame(data["final_frames"][index - 1], canvas_size)
            normalized_frames.append(normalized)
            normalized_path = frames_dir / f"{state}_{index:02d}.png"
            public_normalized_path = public_frames_dir / f"{state}_{index:02d}.png"
            normalized.save(normalized_path)
            normalized.save(public_normalized_path)

        normalized_sheet = state_dir / f"{state}_sheet_normalized.png"
        public_normalized_sheet = public_state_dir / f"{state}_sheet_normalized.png"
        save_sheet(normalized_frames, normalized_sheet)
        save_sheet(normalized_frames, public_normalized_sheet)

        manifest["states"][state] = {
            "source": data["source"],
            "frame_count": len(cropped_frames),
            "scale_factor": 1.0,
            "frame_boxes": data["boxes"],
            "normalized_canvas": {"width": canvas_size[0], "height": canvas_size[1]},
        }

    manifest_path = OUTPUT_ROOT / "manifest.json"
    runtime_manifest_path = OUTPUT_ROOT / "runtime_manifest.json"
    public_manifest_path = PUBLIC_ROOT / "manifest.json"
    public_runtime_manifest_path = PUBLIC_ROOT / "runtime_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    public_manifest_path.write_text(json.dumps(manifest, indent=2))
    runtime_manifest = build_runtime_manifest(manifest)
    runtime_manifest_path.write_text(json.dumps(runtime_manifest, indent=2))
    public_runtime_manifest_path.write_text(json.dumps(runtime_manifest, indent=2))


if __name__ == "__main__":
    main()
