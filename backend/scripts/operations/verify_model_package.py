from __future__ import annotations

from pathlib import Path
import sys


BACKEND_SRC = Path(__file__).resolve().parents[2] / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from decision_workbench.modeling.model_package_verify import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
