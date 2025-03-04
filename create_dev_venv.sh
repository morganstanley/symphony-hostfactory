#!/usr/bin/env bash

# Check if pip is in the path
if ! command -v pip &> /dev/null
then
    echo "pip could not be found"
    python -m ensurepip --upgrade
    python -m pip install --upgrade pip
fi
python -m pip install uv
python -m uv venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
pip install uv
uv pip install -r pyproject.toml -c constraints.txt
uv pip install -e .

echo "Source the venv with: source .venv/bin/activate"
