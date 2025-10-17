"""
Interactive command-line launcher for AQUAH agent runs with a simple
landing page and guided setup.

What this CLI does:
- Shows an ASCII banner and quick-start tips
- Prompts for: OpenAI API key, model selection, and flood period
- Exports values to environment variables and launches the runner
"""
from __future__ import annotations

import os
import sys
from getpass import getpass


ASCII_BANNER = r"""
   ░███      ░██████   ░██     ░██    ░███    ░██     ░██ 
  ░██░██    ░██   ░██  ░██     ░██   ░██░██   ░██     ░██ 
 ░██  ░██  ░██     ░██ ░██     ░██  ░██  ░██  ░██     ░██ 
░█████████ ░██     ░██ ░██     ░██ ░█████████ ░██████████ 
░██    ░██ ░██     ░██ ░██     ░██ ░██    ░██ ░██     ░██ 
░██    ░██  ░██   ░██   ░██   ░██  ░██    ░██ ░██     ░██ 
░██    ░██   ░██████     ░██████   ░██    ░██ ░██     ░██ 
                  ░██                                     
                   ░██                                    
                                                          
Automatic Quantification and Unified Agent in Hydrology                                                          
"""


AVAILABLE_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "claude-4-sonnet-20250514",
    "gemini-2.5-flash-preview-05-20",
    "claude-4-opus-20250514",
]


def prompt_nonempty(prompt_text: str, default: str | None = None) -> str:
    """Prompt until non-empty is provided; honor default if user hits Enter."""
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        if default is not None:
            return default
        print("Please enter a value.")


def print_landing_page() -> None:
    print(ASCII_BANNER)
    print()
    print("Tips for getting started:")
    print("  1. Provide your OpenAI API key (get one at https://platform.openai.com/settings/profile/user)")
    print("  2. Select a model from:")
    for i, name in enumerate(AVAILABLE_MODELS, start=1):
        print(f"     {i}. {name}")
    print("  3. You will specify the flood period during the simulation prompts")
    print()

def prompt_model() -> str:
    """Prompt for a model by index or name with validation."""
    default_model = os.environ.get("OPENAI_MODEL_NAME", AVAILABLE_MODELS[0])
    raw = input(f"Select model [default: {default_model}]: ").strip()
    if not raw:
        return default_model
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(AVAILABLE_MODELS):
            return AVAILABLE_MODELS[idx]
    if raw in AVAILABLE_MODELS:
        return raw
    print("Unrecognized model. Using default.")
    return default_model

def main() -> int:
    print_landing_page()

    # 1) API key (hidden); use existing env if present
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

    # 2) Model selection
    model = prompt_model()

    # Optional: prevent noisy telemetry
    os.environ.setdefault("OTEL_PYTHON_DISABLED", "true")

    # 3) Persist model; flood period will be prompted later by the runner
    os.environ["OPENAI_MODEL_NAME"] = model

    # 4) Run
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


