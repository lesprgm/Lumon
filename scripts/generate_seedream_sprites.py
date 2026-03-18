from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SPRITE_ROOT = ROOT / "dog sprite"
PROMPTS_PATH = SPRITE_ROOT / "PROMPTS.md"
DEFAULT_MODEL = "bytedance-seed/seedream-4.5"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
STATE_ORDER = ("idle", "busy", "success", "error", "locomotion")
STATE_PROMPT_HEADERS = {
    "idle": "## Idle Prompt",
    "busy": "## Busy Prompt",
    "success": "## Success Prompt",
    "error": "## Error Prompt",
    "locomotion": "## Locomotion Prompt",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def extract_prompt(markdown: str, header: str) -> str:
    pattern = re.escape(header) + r"\n```text\n(.*?)\n```"
    match = re.search(pattern, markdown, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not find prompt section for {header}")
    return match.group(1).strip()


def parse_image_url(payload: dict) -> str:
    choice = payload["choices"][0]
    message = choice.get("message", {})

    images = message.get("images") or []
    for image in images:
        image_url = image.get("image_url", {})
        if isinstance(image_url, dict) and image_url.get("url"):
            return image_url["url"]
        if image.get("url"):
            return image["url"]
        if image.get("b64_json"):
            return f"data:image/png;base64,{image['b64_json']}"

    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                return image_url["url"]
            if part.get("type") == "output_image" and part.get("image_url"):
                return part["image_url"]

    raise RuntimeError("No image payload found in OpenRouter response")


def decode_image(image_url: str) -> bytes:
    if image_url.startswith("data:"):
        _, encoded = image_url.split(",", 1)
        return base64.b64decode(encoded)
    with urlopen(image_url, timeout=60) as response:  # noqa: S310
        return response.read()


def build_request(prompt: str, model: str) -> dict:
    return {
        "model": model,
        "modalities": ["image"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }


def generate_state(
    *,
    state: str,
    prompt: str,
    model: str,
    endpoint: str,
    api_key: str,
    variants: int,
) -> list[Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_dir = SPRITE_ROOT / state
    state_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for index in range(1, variants + 1):
        body = build_request(prompt, model)
        body_bytes = json.dumps(body).encode("utf-8")
        request = Request(
            endpoint,
            data=body_bytes,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/leslie/Lumon",
                "X-Title": "Lumon sprite generation",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"OpenRouter request failed: {exc}") from exc

        image_url = parse_image_url(payload)
        image_bytes = decode_image(image_url)

        image_path = state_dir / f"seedream_{state}_{timestamp}_{index:02d}.png"
        image_path.write_bytes(image_bytes)

        metadata_path = state_dir / f"seedream_{state}_{timestamp}_{index:02d}.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "state": state,
                    "model": model,
                    "endpoint": endpoint,
                    "prompt": prompt,
                    "response": payload,
                },
                indent=2,
            )
        )
        outputs.append(image_path)

    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Lumon dog sprite sheets through OpenRouter Seedream.")
    parser.add_argument("--state", choices=[*STATE_ORDER, "all"], default="all")
    parser.add_argument("--variants", type=int, default=1)
    parser.add_argument("--endpoint", default=os.environ.get("OPENROUTER_IMAGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_IMAGE_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Missing OPENROUTER_API_KEY. Add it to .env or your shell environment.", file=sys.stderr)
        return 1

    prompts_markdown = PROMPTS_PATH.read_text()
    states = list(STATE_ORDER) if args.state == "all" else [args.state]

    generated: list[Path] = []
    for state in states:
        prompt = extract_prompt(prompts_markdown, STATE_PROMPT_HEADERS[state])
        generated.extend(
            generate_state(
                state=state,
                prompt=prompt,
                model=args.model,
                endpoint=args.endpoint,
                api_key=api_key,
                variants=args.variants,
            )
        )

    for path in generated:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
