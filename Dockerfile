# Multi-stage build: compile EF5, then assemble Python runtime with AQUAH

# ---- Stage 1: Build EF5 from source (Ubuntu + build deps) ----
FROM ubuntu:22.04 AS ef5-builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    git build-essential gcc make libgeotiff-dev dh-autoreconf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone https://github.com/HyDROSLab/EF5.git
WORKDIR /opt/EF5
RUN autoreconf --force --install \
 && ./configure \
 && sed -i 's/-Werror//g' Makefile \
 && make -j"$(nproc)"

# ---- Stage 2: Python runtime with geospatial stack and AQUAH ----
FROM mambaorg/micromamba:1.5.8-focal

# Create env with geospatial libs via conda-forge for GDAL/PROJ compatibility
USER root
SHELL ["/bin/bash", "-lc"]

# Install system fonts for pandoc PDFs and basic tools
RUN apt-get update && apt-get install -y \
    curl pandoc texlive-xetex lmodern \
    && rm -rf /var/lib/apt/lists/*

# Create env named aquah with pinned gdal/rasterio stack
RUN micromamba create -y -n aquah -c conda-forge \
    python=3.11 \
    gdal \
    rasterio \
    geopandas \
    proj \
    geos \
    shapely \
    cartopy \
    pip \
 && micromamba clean -a -y

ENV MAMBA_DEFAULT_ENV=aquah
ENV PATH=/opt/conda/envs/aquah/bin:$PATH

# Fetch AQUAH from GitHub (no local context dependency)
WORKDIR /app
RUN git clone https://github.com/Flood-Lab/AQUAH.git /app
RUN git checkout dev

# Add minimal CLI if not present upstream
RUN if [ ! -f tools/cli.py ]; then \
    mkdir -p tools; \
    cat > tools/cli.py <<'PY'; \
"""
Interactive command-line launcher for AQUAH agent runs.
Prompts for OpenAI API key and model name, then calls tools.aquah_run.aquah_run.
"""
from __future__ import annotations
import os
import sys
from getpass import getpass

def prompt_nonempty(prompt_text: str, default: str | None = None) -> str:
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        if default is not None:
            return default
        print("Please enter a value.")

def main() -> int:
    print("\n=== AQUAH Agent Runner (CLI) ===\n")
    existing_key = os.environ.get("OPENAI_API_KEY")
    if existing_key:
        print("Detected OPENAI_API_KEY in environment. Press Enter to reuse or type a new key.")
        key_input = getpass("OpenAI API key [hidden] (leave blank to keep): ")
        if key_input.strip():
            os.environ["OPENAI_API_KEY"] = key_input.strip()
    else:
        key_input = getpass("OpenAI API key [hidden]: ")
        if not key_input.strip():
            print("Error: OpenAI API key is required.")
            return 1
        os.environ["OPENAI_API_KEY"] = key_input.strip()
    default_model = os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini")
    model = prompt_nonempty(f"OpenAI model name [default: {default_model}]: ", default=default_model)
    os.environ.setdefault("OTEL_PYTHON_DISABLED", "true")
    try:
        from tools.aquah_run import aquah_run
    except Exception as exc:
        print(f"Failed to import runner: {exc}")
        return 1
    try:
        aquah_run(model)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        return 130
    except Exception as exc:
        print(f"Run failed: {exc}")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PY
  fi

# Install Python packages via pip if requirements.txt exists; otherwise install runtime deps
RUN if [ -f requirements.txt ]; then \
      pip install --no-cache-dir -r requirements.txt; \
    else \
      pip install --no-cache-dir numpy pandas requests PyYAML python-dateutil tqdm folium matplotlib Pillow scikit-learn dataretrieval openai anthropic google-generativeai crewai crewai-tools pypandoc opentelemetry-api; \
    fi

# Provide EF5 binary expected by crest_run.py
RUN mkdir -p /app/EF5/bin
COPY --from=ef5-builder /opt/EF5/bin/ef5 /app/EF5/bin/ef5

# Ensure entry folders exist
RUN mkdir -p /app/CREST_output /app/figures

# Default environment (can be overridden at runtime)
ENV OTEL_PYTHON_DISABLED=true

# Command-line entrypoint: interactive CLI
ENTRYPOINT ["python", "-m", "tools.cli"]
