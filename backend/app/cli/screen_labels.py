"""Developer tool (D-040): write `app/pipeline/screen_labels.json`, the CLIP text embeddings of
the screen type labels, so the app only needs the image encoder at runtime.

Usage (from `backend/`, needs the text model and tokenizer of the pinned revision):
    python -m app.cli.screen_labels <folder with onnx/text_model_quantized.onnx and
    tokenizer.json>

The folder can be filled with `huggingface_hub.hf_hub_download(SCREEN_MODEL_REPO, name,
revision=SCREEN_MODEL_REVISION, local_dir=folder)` for `onnx/text_model_quantized.onnx` and
`tokenizer.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

from app.config import SCREEN_MODEL_REPO, SCREEN_MODEL_REVISION
from app.pipeline.screen import LABELS_PATH

# The labels, grouped. Chosen on the owner's videos (Valorant stream with overlay, Valorant
# video, Minecraft, a product review, Hindi sketches): a stream overlay matched "stream starting
# soon", so that label was dropped and a webcam-in-the-corner gameplay label added.
LABELS: dict[str, list[str]] = {
    "gameplay": [
        "a first person shooter game with a gun in the player's hands",
        "video game gameplay with a health bar and a minimap",
        "a minecraft world with blocks",
        "a car driving in a city in a video game",
        "a video game character running through a level",
        "a fight in a video game",
        "a video game with a streamer's webcam in the corner",
        "a screenshot of an action video game",
    ],
    "menu": [
        "a video game main menu with buttons",
        "a character select screen with a grid of character portraits",
        "a video game agent select screen with a hero standing in the middle",
        "a video game lobby with player cards",
        "a video game loading screen with player names",
        "a video game scoreboard table with player names and numbers",
        "a video game settings menu",
        "a video game store with items for sale",
        "a video game inventory screen with item icons",
    ],
    "waiting": ["an empty black screen"],
    "talking": [
        "people talking into microphones on a podcast",
        "a person talking to the camera",
        "a person reviewing a product",
    ],
    "other": [
        "a computer desktop with windows",
        "a web page",
        "a presentation slide",
        "a street outdoors",
    ],
}
EMBEDDING_DECIMALS = 5


def main(argv: list[str] | None = None) -> int:
    from tokenizers import Tokenizer  # dev-only; installed with faster-whisper

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    folder: Path = args.folder
    model = folder / "onnx" / "text_model_quantized.onnx"
    tokenizer_file = folder / "tokenizer.json"
    if not model.is_file() or not tokenizer_file.is_file():
        print(f"ERROR: {model} or {tokenizer_file} is missing", file=sys.stderr)
        return 1
    tokenizer = Tokenizer.from_file(str(tokenizer_file))
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    pairs = [(group, prompt) for group, prompts in LABELS.items() for prompt in prompts]
    encoded = [tokenizer.encode(prompt).ids for _, prompt in pairs]
    width = max(len(ids) for ids in encoded)
    # Padded with the end token: the text model pools at the end token (the highest id).
    end_token = encoded[0][-1]
    ids = np.array([e + [end_token] * (width - len(e)) for e in encoded], dtype=np.int64)
    embeddings = session.run(None, {"input_ids": ids})[0]
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    data = {
        "model": SCREEN_MODEL_REPO,
        "revision": SCREEN_MODEL_REVISION,
        "labels": [
            {
                "group": group,
                "prompt": prompt,
                "embedding": [round(float(v), EMBEDDING_DECIMALS) for v in vector],
            }
            for (group, prompt), vector in zip(pairs, embeddings, strict=True)
        ],
    }
    LABELS_PATH.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(pairs)} labels -> {LABELS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
