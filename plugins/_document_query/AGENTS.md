# Document Query Plugin DOX

## Purpose

- Own document loading, parsing, indexing, and Q&A over local and remote documents.

## Ownership

- `tools/document_query.py` owns the agent-facing query tool.
- `helpers/` owns fetching, parser routing, indexing, and query orchestration.
- `prompts/` owns document-query tool and optimization prompts.
- `hooks.py`, `default_config.yaml`, `plugin.yaml`, `skills/`, `extensions/`, and `webui/` own dependency bootstrap, settings, metadata, skill guidance, hooks, and UI.

## Local Contracts

- Respect configured size, timeout, retry, parser concurrency, and OCR limits.
- A local document path goes through `helpers.workdir.confine_path`: the calling account's working directory, uploads, downloads, skills and the projects folder, and nothing else.
- Fetch local and remote resources safely and avoid leaking document contents beyond intended prompt/tool use.
- The plugin installs nothing. `hooks.ensure_dependencies` only says whether the parser (`liteparse`, a line in the root `requirements.txt`, installed when the image is built) can be imported; when it cannot, that is reported and the other parsers are used. Nothing is fetched at runtime (`tests/test_nothing_is_fetched_at_runtime.py`).

## Work Guidance

- Coordinate parser changes with timeout handling and fallback behavior.
- A parser claims a type only when it can read it here: `LiteParseParser.can_handle` leaves office documents and images to the next parser unless LibreOffice or ImageMagick is on the path. A parser that fails is logged as an error, so one that always fails for a type is noise on every such document.
- An image is read by Tesseract (`ImageParser`). `UnstructuredLoader` reads an image only after fetching a layout model from the Hugging Face Hub, which a delivered machine cannot do.
- Keep prompt and skill guidance clear that image files, screenshots, scans, charts, photos, and diagrams should go to vision tools first when available; `document_query` image OCR is a text-focused fallback when vision is unavailable or cannot read the needed text.

## Verification

- Smoke-test representative PDF, text/HTML, image/OCR, and remote-document queries after parser changes.

## Child DOX Index

No child DOX files.
