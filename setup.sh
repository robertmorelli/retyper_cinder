#!/bin/sh
set -eu

cd "$(dirname "$0")"
git submodule update --init -- cinderx
uv python install 3.14
uv venv --clear --python 3.14 .venv
uv pip install --python .venv/bin/python ./cinderx scipy
