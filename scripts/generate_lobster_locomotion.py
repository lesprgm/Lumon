from __future__ import annotations

from pathlib import Path
from PIL import Image

ROOT = Path('/Users/leslie/Documents/Lumon')
CLEANED = ROOT / 'lobster sprite' / 'cleaned'
PUBLIC = ROOT / 'frontend' / 'public' / 'assets' / 'lobster'
RUNTIME_MANIFEST = CLEANED / 'runtime_manifest.json'
CANVAS_SIZE = (747, 571)
SOURCE_FRAMES = [
    CLEANED / 'idle' / 'frames' / 'idle_01.png',
    CLEANED / 'idle' / 'frames' / 'idle_02.png',
    CLEANED / 'idle' / 'frames' / 'idle_03.png',
    CLEANED / 'idle' / 'frames' / 'idle_04.png',
    CLEANED / 'idle' / 'frames' / 'idle_05.png',
    CLEANED / 'idle' / 'frames' / 'idle_06.png',
]
POSES = [
    {'dx': -3, 'dy': 0, 'sx': 1.02, 'sy': 0.98, 'rotate': -2.0},
    {'dx': -1, 'dy': -2, 'sx': 1.0, 'sy': 1.0, 'rotate': -0.8},
    {'dx': 2, 'dy': 0, 'sx': 1.02, 'sy': 0.98, 'rotate': 1.8},
    {'dx': 3, 'dy': 0, 'sx': 1.02, 'sy': 0.98, 'rotate': 2.0},
    {'dx': 1, 'dy': -2, 'sx': 1.0, 'sy': 1.0, 'rotate': 0.8},
    {'dx': -2, 'dy': 0, 'sx': 1.02, 'sy': 0.98, 'rotate': -1.8},
]


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel('A')
    bbox = alpha.getbbox()
    if bbox is None:
        raise RuntimeError('No alpha bbox found in source frame')
    return bbox


def transform_frame(image: Image.Image, pose: dict[str, float]) -> Image.Image:
    bbox = alpha_bbox(image)
    sprite = image.crop(bbox)
    scaled = sprite.resize(
        (max(1, round(sprite.width * pose['sx'])), max(1, round(sprite.height * pose['sy']))),
        Image.Resampling.NEAREST,
    )
    rotated = scaled.rotate(pose['rotate'], resample=Image.Resampling.NEAREST, expand=True)
    canvas = Image.new('RGBA', CANVAS_SIZE, (0, 0, 0, 0))
    x = (CANVAS_SIZE[0] - rotated.width) // 2 + int(pose['dx'])
    y = CANVAS_SIZE[1] - rotated.height - 32 + int(pose['dy'])
    canvas.alpha_composite(rotated, (x, y))
    return canvas


def save_sheet(frames: list[Image.Image], output_path: Path) -> None:
    if not frames:
        return
    width = sum(frame.width for frame in frames)
    height = max(frame.height for frame in frames)
    sheet = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    cursor = 0
    for frame in frames:
        sheet.alpha_composite(frame, (cursor, 0))
        cursor += frame.width
    sheet.save(output_path)


def main() -> None:
    frames: list[Image.Image] = []
    cleaned_dir = CLEANED / 'locomotion' / 'frames'
    public_dir = PUBLIC / 'locomotion' / 'frames'
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    for existing in cleaned_dir.glob('*.png'):
        existing.unlink()
    for existing in public_dir.glob('*.png'):
        existing.unlink()

    for index, (source_path, pose) in enumerate(zip(SOURCE_FRAMES, POSES, strict=True), start=1):
        base = Image.open(source_path).convert('RGBA')
        frame = transform_frame(base, pose)
        frames.append(frame)
        filename = f'locomotion_{index:02d}.png'
        frame.save(cleaned_dir / filename)
        frame.save(public_dir / filename)

    save_sheet(frames, CLEANED / 'locomotion' / 'locomotion_sheet_normalized.png')
    save_sheet(frames, PUBLIC / 'locomotion' / 'locomotion_sheet_normalized.png')

    # Transparent sheet for symmetry with the other states.
    save_sheet(frames, CLEANED / 'locomotion' / 'locomotion_sheet_transparent.png')
    save_sheet(frames, PUBLIC / 'locomotion' / 'locomotion_sheet_transparent.png')


if __name__ == '__main__':
    main()
