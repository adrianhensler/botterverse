from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .export_utils import attach_signature
from .store_factory import build_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Botterverse dataset to JSON.")
    parser.add_argument("--output", default="export.json", help="Path to write the export JSON.")
    args = parser.parse_args()

    store = build_store()
    dataset = store.export_dataset()
    secret = os.getenv("BOTTERVERSE_EXPORT_SECRET")
    if secret:
        attach_signature(dataset, secret)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
