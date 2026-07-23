from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.data.dataset_profile import _load_profile_document  # noqa: E402


def materialize(source: Path, output: Path, *, replace: bool = False) -> None:
    if output.exists() and output.resolve() != source.resolve() and not replace:
        raise FileExistsError(f"refusing to replace existing profile: {output}")
    document = _load_profile_document(source)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Expand Dataset Profile inheritance into one standalone JSON document."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--replace", action="store_true")
    arguments = parser.parse_args()
    materialize(arguments.source, arguments.output, replace=arguments.replace)
