#!/bin/bash

# Hifz - Launcher
# Auto-activates conda environment and runs the app

# Find conda installation
CONDA_PATH=$(which conda 2>/dev/null)
if [ -z "$CONDA_PATH" ]; then
    CONDA_PATH="$HOME/anaconda3/bin/conda"
fi

CONDA_BASE=$(dirname $(dirname "$CONDA_PATH"))

if [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    conda activate hifz
    python app.py
else
    echo "Error: Conda not found. Please ensure 'hifz' environment is active."
    python app.py
fi
