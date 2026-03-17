from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPRITE_ROOT = ROOT / "lobster sprite"
OUTPUT_ROOT = SPRITE_ROOT / "cleaned"
STATE_FILES = {
    "idle": SPRITE_ROOT / "idle" / "Gemini_Generated_Image_z78a34z78a34z78a.png",
    "busy": SPRITE_ROOT / "busy" / "Gemini_Generated_Image_y7dv63y7dv63y7dv-2.png",
    "success": SPRITE_ROOT / "success" / "Gemini_Generated_Image_lrve0klrve0klrve.png",
    "error": SPRITE_ROOT / "error" / "Gemini_Generated_Image_ycpn0fycpn0fycpn.png",
}
STATE_FRAME_LIMITS = {
    # The final detected idle segment is clipped in the source art.
    "idle": 7,
}
STATE_PLAYBACK = {
    "idle": {"fps": 8, "loop": True, "hold_last_frame_ms": 0},
    "busy": {"fps": 10, "loop": True, "hold_last_frame_ms": 0},
    "success": {"fps": 9, "loop": False, "hold_last_frame_ms": 450},
    "error": {"fps": 9, "loop": False, "hold_last_frame_ms": 700},
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


def background_candidates(arr: np.ndarray, palette: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    brightest = rgb.max(axis=2)
    channel_spread = rgb.max(axis=2) - rgb.min(axis=2)
    distances = ((rgb[:, :, None, :] - palette[None, None, :, :]) ** 2).sum(axis=3)
    min_distance = distances.min(axis=2)
    return (brightest >= 120) & (channel_spread <= 40) & (min_distance <= 40**2)


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


def alpha_mask_from_background(arr: np.ndarray) -> np.ndarray:
    palette = border_palette(arr)
    background = flood_fill_background(background_candidates(arr, palette))
    alpha = np.where(background, 0, 255).astype(np.uint8)
    return alpha


def defringe(arr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    palette = border_palette(arr)
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

    fringe = opaque & touches_transparent & (channel_spread <= 50) & (min_distance <= 60**2)
    cleaned_alpha = alpha.copy()
    cleaned_alpha[fringe] = 0
    return cleaned_alpha


def frame_boxes(alpha: np.ndarray) -> list[FrameBox]:
    has_foreground = alpha > 0
    non_empty_columns = has_foreground.any(axis=0)
    boxes: list[FrameBox] = []
    start: int | None = None

    for x, present in enumerate(non_empty_columns):
        if present and start is None:
            start = x
        elif not present and start is not None:
            boxes.append(box_for_column_range(has_foreground, start, x))
            start = None
    if start is not None:
        boxes.append(box_for_column_range(has_foreground, start, has_foreground.shape[1]))

    return split_wide_boxes(has_foreground, boxes)


def box_for_column_range(mask: np.ndarray, x0: int, x1: int) -> FrameBox:
    cropped = mask[:, x0:x1]
    rows = cropped.any(axis=1)
    y_indices = np.flatnonzero(rows)
    return FrameBox(x0=x0, y0=int(y_indices[0]), x1=x1, y1=int(y_indices[-1]) + 1)


def split_wide_boxes(mask: np.ndarray, boxes: list[FrameBox]) -> list[FrameBox]:
    if len(boxes) < 2:
        return boxes

    widths = np.array([box.width for box in boxes], dtype=np.int32)
    median_width = float(np.median(widths))
    refined: list[FrameBox] = []

    for box in boxes:
        if box.width <= median_width * 1.45:
            refined.append(box)
            continue

        parts = max(2, round(box.width / median_width))
        refined.extend(split_box_on_valleys(mask, box, parts))

    return refined


def split_box_on_valleys(mask: np.ndarray, box: FrameBox, parts: int) -> list[FrameBox]:
    region = mask[:, box.x0 : box.x1]
    column_density = region.sum(axis=0)
    width = region.shape[1]
    window = max(16, width // (parts * 6))

    cut_points: list[int] = []
    for split_index in range(1, parts):
        ideal = round((width * split_index) / parts)
        left = max(1, ideal - window)
        right = min(width - 1, ideal + window)
        relative_cut = left + int(np.argmin(column_density[left:right]))
        cut_points.append(relative_cut)

    frame_boxes_out: list[FrameBox] = []
    local_start = 0
    for local_end in cut_points + [width]:
        part = box_for_column_range(mask, box.x0 + local_start, box.x0 + local_end)
        frame_boxes_out.append(part)
        local_start = local_end

    return frame_boxes_out


def normalize_frame(frame: Image.Image, canvas_size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - frame.width) // 2
    y = canvas_size[1] - frame.height
    canvas.alpha_composite(frame, (x, y))
    return canvas


def scale_frame(frame: Image.Image, scale: float) -> Image.Image:
    if abs(scale - 1.0) < 1e-6:
        return frame
    width = max(1, round(frame.width * scale))
    height = max(1, round(frame.height * scale))
    return frame.resize((width, height), Image.Resampling.NEAREST)


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
        "sprite_id": "lumon_lobster_v1",
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
            "priority": ["error", "success", "busy", "idle"],
            "default": "idle",
        },
        "recommended_transitions": {
            "on_success_complete": {"next_animation": "idle", "delay_ms": 450},
            "on_error_complete": {"next_animation": "idle", "delay_ms": 700},
        },
    }


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    extracted: dict[str, dict[str, object]] = {}
    global_max_width = 0
    global_max_height = 0

    for state, path in STATE_FILES.items():
        image = Image.open(path).convert("RGBA")
        arr = np.array(image)
        alpha = alpha_mask_from_background(arr)
        alpha = defringe(arr, alpha)
        cleaned = arr.copy()
        cleaned[:, :, 3] = alpha
        cleaned_image = Image.fromarray(cleaned)

        boxes = frame_boxes(alpha)
        frame_limit = STATE_FRAME_LIMITS.get(state)
        if frame_limit is not None:
            boxes = boxes[:frame_limit]
        cropped_frames: list[Image.Image] = []
        manifest_boxes: list[dict[str, int]] = []

        for box in boxes:
            frame = cleaned_image.crop((box.x0, box.y0, box.x1, box.y1))
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

        extracted[state] = {
            "cleaned_image": cleaned_image,
            "cropped_frames": cropped_frames,
            "boxes": manifest_boxes,
            "source": str(path.relative_to(ROOT)),
        }

    non_busy_heights = [frame.height for state, data in extracted.items() if state != "busy" for frame in data["cropped_frames"]]
    target_height = int(round(float(np.median(non_busy_heights)))) if non_busy_heights else 0
    busy_heights = [frame.height for frame in extracted["busy"]["cropped_frames"]]
    busy_median = float(np.median(busy_heights)) if busy_heights else 1.0
    busy_scale = target_height / busy_median if busy_median else 1.0
    extracted["busy"]["final_frames"] = [scale_frame(frame, busy_scale) for frame in extracted["busy"]["cropped_frames"]]
    extracted["busy"]["scale_factor"] = busy_scale

    for state, data in extracted.items():
        if state != "busy":
            data["final_frames"] = list(data["cropped_frames"])
            data["scale_factor"] = 1.0
        for frame in data["final_frames"]:
            global_max_width = max(global_max_width, frame.width)
            global_max_height = max(global_max_height, frame.height)

    canvas_size = (global_max_width + 32, global_max_height + 32)
    manifest: dict[str, object] = {"canvas_size": {"width": canvas_size[0], "height": canvas_size[1]}, "states": {}}

    for state, data in extracted.items():
        state_dir = OUTPUT_ROOT / state
        frames_dir = state_dir / "frames"
        cropped_dir = state_dir / "frames_cropped"
        state_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        cropped_dir.mkdir(parents=True, exist_ok=True)
        clear_png_outputs(frames_dir)
        clear_png_outputs(cropped_dir)

        cleaned_path = state_dir / f"{state}_sheet_transparent.png"
        data["cleaned_image"].save(cleaned_path)

        normalized_frames: list[Image.Image] = []
        cropped_frames: list[Image.Image] = data["cropped_frames"]
        for index, frame in enumerate(cropped_frames, start=1):
            cropped_path = cropped_dir / f"{state}_{index:02d}.png"
            frame.save(cropped_path)

            normalized = normalize_frame(data["final_frames"][index - 1], canvas_size)
            normalized_frames.append(normalized)
            normalized.save(frames_dir / f"{state}_{index:02d}.png")

        save_sheet(normalized_frames, state_dir / f"{state}_sheet_normalized.png")

        manifest["states"][state] = {
            "source": data["source"],
            "frame_count": len(cropped_frames),
            "scale_factor": round(float(data["scale_factor"]), 4),
            "frame_boxes": data["boxes"],
            "normalized_canvas": {"width": canvas_size[0], "height": canvas_size[1]},
        }

    (OUTPUT_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    runtime_manifest = build_runtime_manifest(manifest)
    (OUTPUT_ROOT / "runtime_manifest.json").write_text(json.dumps(runtime_manifest, indent=2))


if __name__ == "__main__":
    main()
