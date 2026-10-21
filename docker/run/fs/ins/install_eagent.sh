#!/bin/bash
set -e

# Rezztor EAgent source is supplied by the Docker build context, never fetched.
# Upstream cloned https://github.com/Rezztor/netops-eagent here at build time; we
# removed that both to sever the dependency and because this repository is
# private, so a build-time clone could not authenticate anyway.

SOURCE_DIR="/git/eagent"

if [ ! -f "$SOURCE_DIR/run_ui.py" ]; then
    echo "CRITICAL ERROR: no source at $SOURCE_DIR." >&2
    echo "The Dockerfile must COPY the repository there before running this script." >&2
    exit 1
fi

echo "Using Rezztor EAgent source from $SOURCE_DIR"

. "/ins/setup_venv.sh" "$@"

# Install Python packages. CPU-only torch first: resolving it from PyPI drags in
# ~5 GB of nvidia CUDA wheels that this deployment never uses. By version: the
# base image holds 2.4.0, an unversioned request is satisfied by that, and
# unstructured-inference 1.6 needs 2.10 or later, which the requirements would
# then take from PyPI. These two are what the suite runs on.
uv pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1 torchvision==0.29.1
uv pip install -r "$SOURCE_DIR/requirements.txt"
# A requirement that asks for another torch replaces the CPU-only one and brings them after all.
if uv pip list | grep -i '^nvidia'; then
    echo "CRITICAL ERROR: the requirements pulled in the CUDA wheels listed above." >&2
    exit 1
fi

# The document parser (unstructured: HTML, and every format with no parser of
# its own) reads a spaCy model. When it is not installed, the first such
# document asks github.com for it at run time, and on a network without the
# internet is not parsed at all. requirements.txt installs it; this proves the
# parser finds the one it looks for, by the name and version it uses itself.
python -c "
import spacy
from unstructured.nlp import tokenize
model = spacy.load(tokenize._SPACY_MODEL_NAME)
assert model.meta['version'] == tokenize._SPACY_MODEL_VERSION, model.meta['version']
"

# Preload models so the first container start does not download them.
python "$SOURCE_DIR/preload.py" --dockerized=true
