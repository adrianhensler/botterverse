from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .export_utils import verify_signature
from .store_factory import build_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a Botterverse dataset from JSON.")
    parser.add_argument("--input", required=True, help="Path to the export JSON.")
    args = parser.parse_args()

    input_path = Path(args.input)
    payload = json.loads(input_path.read_text())

    secret = os.getenv("BOTTERVERSE_EXPORT_SECRET")
    if secret:
        verify_signature(payload, secret)

    store = build_store()
    store.import_dataset(payload)


if __name__ == "__main__":
    main()
