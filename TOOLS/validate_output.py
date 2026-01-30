import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("Missing dependency: jsonschema. Install with: pip install jsonschema")
    sys.exit(1)

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def validate(instance_path: str, schema_path: str) -> None:
    instance = load_json(Path(instance_path))
    schema = load_json(Path(schema_path))
    jsonschema.validate(instance=instance, schema=schema)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python TOOLS/validate_output.py <output.json> <schema.json>")
        sys.exit(1)

    out_path, schema_path = sys.argv[1], sys.argv[2]
    validate(out_path, schema_path)
    print("OK: Output conforms to schema.")
