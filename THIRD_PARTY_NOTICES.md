# Third-party notices

Kliprix is MIT licensed (see `LICENSE`). It uses the components below; each keeps its own
license. No GPL code is vendored in this repository. Python and npm packages are installed
from PyPI/npm on the user's machine; two of them are LGPL (`soxr`, and the FFmpeg libraries
inside the `av` wheels). Generated from the installed package metadata on 2026-11-18;
regenerate when dependencies change.

## Not bundled (installed by the user)
| Component | License | Note |
|---|---|---|
| FFmpeg / ffprobe | LGPL-2.1+ or GPL-2+ depending on the build | Installed separately; Kliprix calls it as a program and never redistributes it |
| Node.js | MIT | Runs the frontend and is yt-dlp's JavaScript runtime |
| Python | PSF-2.0 | Runs the backend |

## Fonts (bundled in `backend/app/assets/fonts/`, license files next to them)
| Font | License | Source |
|---|---|---|
| Anton Regular | SIL Open Font License 1.1 (`OFL-Anton.txt`) | github.com/googlefonts/AntonFont via google/fonts |
| Montserrat ExtraBold | SIL Open Font License 1.1 (`OFL-Montserrat.txt`) | github.com/JulietaUla/Montserrat |
| Noto Sans Devanagari Bold | SIL Open Font License 1.1 (`OFL-NotoSansDevanagari.txt`) | github.com/notofonts/devanagari |

## Models (downloaded on first use into `data/models/`, not in the repository)
| Model | License | Source |
|---|---|---|
| faster-whisper CTranslate2 conversions of OpenAI Whisper (tiny/base/small/medium/large-v3) | MIT | huggingface.co/Systran |
| MediaPipe BlazeFace short-range face detector | Apache-2.0 | storage.googleapis.com/mediapipe-models |
| Silero VAD (inside faster-whisper) | MIT | github.com/snakers4/silero-vad |
| PaddleOCR PP-OCRv4 detection/recognition + mobile v2 direction models, inside the `rapidocr-onnxruntime` wheel (on-screen text, D-045) | Apache-2.0 | github.com/RapidAI/RapidOCR; github.com/PaddlePaddle/PaddleOCR |
| CED-mini audio tagging (AudioSet), sherpa-onnx int8 conversion (sound events, D-041) | Apache-2.0 | github.com/RicherMans/CED; github.com/k2-fsa/sherpa-onnx releases `audio-tagging-models` |
| OpenAI CLIP ViT-B/32 image encoder, int8 ONNX export (screen type, D-040); the label text embeddings in `backend/app/pipeline/screen_labels.json` come from its text encoder | MIT | huggingface.co/Xenova/clip-vit-base-patch32 (revision 6b7c5237) |

## Python packages (backend runtime, `backend/requirements.txt`)
| Package | Version | License |
|---|---|---|
| absl-py | 2.5.1 | Apache-2.0 |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.6 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| av | 19.0.2 | BSD-3-Clause |
| boto3 | 1.44.5 | Apache-2.0 |
| botocore | 1.44.5 | Apache-2.0 |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| cloudpickle | 3.1.3 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| contourpy | 1.4.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| ctranslate2 | 4.9.0 | MIT |
| cycler | 0.12.1 | BSD License |
| decorator | 5.3.1 | BSD-2-Clause |
| fastapi | 0.163.1 | MIT |
| faster-whisper | 1.2.1 | MIT License |
| fastuuid | 0.14.0 | BSD License |
| filelock | 4.2.12 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fonttools | 4.68.0 | MIT |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT License |
| h2 | 4.4.1 | MIT |
| hf-xet | 1.8.0 | Apache-2.0 |
| hpack | 4.2.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD License |
| huggingface_hub | 2.4.0 | Apache Software License |
| hyperframe | 6.1.0 | MIT License |
| idna | 3.20 | BSD-3-Clause |
| importlib_metadata | 8.9.0 | Apache-2.0 |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.19.0 | MIT |
| jmespath | 1.1.0 | MIT License |
| joblib | 1.6.0 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| kiwisolver | 1.5.1 | BSD License |
| lazy-loader | 0.6 | BSD-3-Clause |
| librosa | 1.0.1 | ISC License (ISCL) |
| litellm | 1.110.0 | MIT |
| llvmlite | 0.50.0 | BSD-2-Clause AND Apache-2.0 WITH LLVM-exception |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| mediapipe | 1.2.3 | Apache Software License |
| msgpack | 1.2.4 | Apache-2.0 |
| multidict | 7.2.1 | Apache-2.0 |
| narwhals | 2.29.0 | MIT |
| numba | 0.68.1 | BSD License |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| onnxruntime | 1.32.0 | MIT License |
| openai | 3.55.0 | Apache-2.0 |
| opencv-contrib-python | 5.1.0.95 | Apache Software License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU |
| platformdirs | 4.14.7 | MIT |
| pooch | 1.9.0 | BSD-3-Clause |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| pydantic-settings | 2.16.3 | MIT |
| pyparsing | 3.3.3 | MIT |
| python-dateutil | 2.9.0.post0 | BSD License, Apache Software License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT License |
| referencing | 0.37.0 | MIT |
| regex | 2026.11.18 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache Software License |
| rpds-py | 2026.9.1 | MIT |
| s3transfer | 0.19.3 | Apache Software License |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| scipy | 1.18.1 | BSD License |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License, Apache Software License |
| sounddevice | 0.5.6 | MIT |
| soundfile | 0.14.0 | BSD License |
| soxr | 1.1.0 | LGPL-2.1-or-later |
| SQLAlchemy | 2.1.9 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| threadpoolctl | 3.8.0 | BSD-3-Clause |
| tiktoken | 0.14.0 | MIT License |
| tokenizers | 0.23.3 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.5 | MIT |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| watchfiles | 1.3.0 | MIT License |
| websockets | 17.4 | BSD-3-Clause |
| yarl | 1.25.1 | Apache-2.0 |
| yt-dlp | 2026.10.26 | Unlicense |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC |
| zipp | 4.1.2 | MIT |

## JavaScript packages (frontend runtime, `frontend/package.json` dependencies)
| Package | Version | License |
|---|---|---|
| @hookform/resolvers | 7.0.7 | MIT |
| @tanstack/react-query | 5.108.0 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| cn | 0.5.2 | MIT |
| lucide-react | 1.75.0 | ISC |
| next | 16.4.6 | MIT |
| next-themes | 0.4.6 | MIT |
| radix-ui | 1.8.1 | MIT |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| react-hook-form | 7.92.0 | MIT |
| shadcn | 4.23.0 | MIT |
| sonner | 2.0.8 | MIT |
| tw-animate-css | 1.4.0 | MIT |
| zod | 4.6.7 | MIT |

Development-only tools (ruff, mypy, pytest, ESLint, Prettier, TypeScript, Vitest, openapi-typescript) are not shipped with the app.
