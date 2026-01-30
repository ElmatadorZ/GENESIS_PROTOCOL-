from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import jsonschema

from providers import get_provider
from providers.base import ProviderError


DEFAULT_SYSTEM_PROMPT = "./PROMPTS/genesis_protocol_system.prompt.md"


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return p.read_text(encoding="utf-8")


def extract_json(text: str) -> str:
    """
    Extract the first JSON object from the response.
    Works even if the model accidentally adds extra text.
    """
    text = text.strip()

    # If already pure JSON
    if text.startswith("{") and text.endswith("}"):
        return text

    # Try to find JSON object with a balanced-ish regex heuristic
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError("No JSON object found in model output.")
    return m.group(0)


def validate_json_schema(instance: dict, schema_path: str) -> None:
    schema = json.loads(read_text(schema_path))
    jsonschema.validate(instance=instance, schema=schema)


def main():
    load_dotenv()  # loads .env if present

    ap = argparse.ArgumentParser(
        description="Genesis Protocol CLI — run Genesis OS prompt against any LLM provider and validate canonical JSON outputs."
    )
    ap.add_argument("--provider", default=os.getenv("GENESIS_PROVIDER", "openai"),
                    help="openai | anthropic | gemini | ollama")
    ap.add_argument("--system", default=os.getenv("GENESIS_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
                    help="Path to system prompt file")
    ap.add_argument("--input", required=True,
                    help="Path to a user prompt text file")
    ap.add_argument("--schema", default="./SCHEMAS/DecisionRecord.schema.json",
                    help="Path to JSON schema to validate output against")
    ap.add_argument("--out", default="./output.json",
                    help="Where to write the extracted JSON output")
    ap.add_argument("--no-validate", action="store_true",
                    help="Skip JSON schema validation")
    ap.add_argument("--dry-run", action="store_true",
                    help="Do not call provider; print combined prompt and exit")

    args = ap.parse_args()

    system_prompt = read_text(args.system)
    user_prompt = read_text(args.input)

    # Strong enforcement hint (works across providers)
    user_prompt = (
        user_prompt.strip()
        + "\n\nOUTPUT REQUIREMENT: Return JSON only. No markdown. Must conform to the schema."
    )

    if args.dry_run:
        print("=== SYSTEM PROMPT ===")
        print(system_prompt)
        print("\n=== USER PROMPT ===")
        print(user_prompt)
        return

    try:
        provider = get_provider(args.provider)
        resp = provider.chat(system=system_prompt, user=user_prompt)
    except ProviderError as e:
        raise SystemExit(f"[ProviderError] {e}")

    raw_text = resp.text
    json_text = extract_json(raw_text)

    try:
        instance = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[JSONDecodeError] Could not parse JSON: {e}\n\nRaw:\n{json_text}")

    if not args.no_validate:
        try:
            validate_json_schema(instance, args.schema)
        except jsonschema.ValidationError as e:
            raise SystemExit(f"[SchemaValidationError] Output does not match schema:\n{e}")

    Path(args.out).write_text(json.dumps(instance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: wrote {args.out} (provider={args.provider}, schema={args.schema})")


if __name__ == "__main__":
    main()
