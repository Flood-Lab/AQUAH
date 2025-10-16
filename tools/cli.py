"""
Interactive command-line launcher for AQUAH agent runs.

Prompts for:
- OpenAI API key (hidden input, optional if already set in env)
- LLM model name (e.g., gpt-4o, gpt-4o-mini)

Then invokes tools.aquah_run.aquah_run(model_name), which will interactively
ask for the simulation scenario details.
"""
from __future__ import annotations

import os
import sys
from getpass import getpass


def prompt_nonempty(prompt_text: str, default: str | None = None) -> str:
    """Prompt until non-empty is provided; honor default if user hits Enter."""
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        if default is not None:
            return default
        print("Please enter a value.")


def main() -> int:
    print("\n=== AQUAH Agent Runner (CLI) ===\n")

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

    # 2) Model name (default)
    default_model = os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini")
    model = prompt_nonempty(f"OpenAI model name [default: {default_model}]: ", default=default_model)

    # Optional: prevent noisy telemetry
    os.environ.setdefault("OTEL_PYTHON_DISABLED", "true")

    # 3) Run
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


