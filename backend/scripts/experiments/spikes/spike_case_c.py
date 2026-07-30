"""反証ケースC：可変長温度系列Task（型の特定のみ）。

実装はしない。現行契約で可変長系列を表現できないことを具体的に確認し、
本当に必要な新しい型の最小集合を確定させる。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("SPIKE_REPO_ROOT") or Path.cwd()).resolve()
if not (REPO / "pyproject.toml").exists():
    raise SystemExit(f"run from the repository root (got {REPO})")
sys.path.insert(0, str(REPO / "backend" / "src"))


def _rejected(label: str, thunk) -> tuple[bool, str]:
    try:
        thunk()
    except Exception as exc:
        first = str(exc).splitlines()
        return True, next(
            (line.strip() for line in first if "Value error" in line or "value" in line.lower()),
            first[0].strip() if first else type(exc).__name__,
        )
    return False, "受理された"


def main() -> int:
    from material_workbench.contracts.schemas import CandidateInputs, HeatPoint
    from material_workbench.contracts.task_contracts import (
        CanonicalCandidate,
        InputFieldDefinition,
        InputGroupDefinition,
        NumericRange,
        TaskDefinition,
    )

    results: list[tuple[str, bool, str]] = []

    # 1. heat_pattern 以外の系列group を宣言できるか
    rejected, detail = _rejected(
        "series group",
        lambda: InputGroupDefinition(
            key="temperature_profile",  # type: ignore[arg-type]
            order=0,
            label="温度履歴",
            fields=(
                InputFieldDefinition(
                    path="temperature_profile",
                    kind="heat_pattern",
                    order=0,
                    label="温度履歴",
                ),
            ),
        ),
    )
    results.append(("heat_pattern以外の系列groupを宣言できる", not rejected, detail))

    # 2. 系列fieldのpathを heat_pattern 以外にできるか
    rejected, detail = _rejected(
        "series path",
        lambda: InputFieldDefinition(
            path="series.thermal_cycle",
            kind="heat_pattern",
            order=0,
            label="熱サイクル",
        ),
    )
    results.append(("系列fieldのpathを自由に付けられる", not rejected, detail))

    # 3. 系列に単位を宣言できるか
    rejected, detail = _rejected(
        "series unit",
        lambda: InputFieldDefinition(
            path="heat_pattern",
            kind="heat_pattern",
            order=0,
            label="温度履歴",
            unit="°F",
        ),
    )
    results.append(("系列fieldに単位を宣言できる", not rejected, detail))

    # 4. 30点を超える系列を候補入力として保存できるか
    long_series = [
        HeatPoint(time_s=float(index), temperature_c=25.0 + index * 0.5) for index in range(64)
    ]
    rejected, detail = _rejected(
        "long series",
        lambda: CandidateInputs(
            composition={}, process={"ls_mpm": 100.0}, heat_pattern=long_series
        ),
    )
    results.append(("31点以上の可変長系列を候補入力に保存できる", not rejected, detail))
    print(f"[limit] CandidateInputs.heat_pattern の上限点数を確認: 64点 -> {'拒否' if rejected else '受理'}")

    # 5. timestamp重複を「不適格」として保持できるか（値は残しつつ品質判定したい）
    duplicated = [
        HeatPoint(time_s=0.0, temperature_c=25.0),
        HeatPoint(time_s=10.0, temperature_c=400.0),
        HeatPoint(time_s=10.0, temperature_c=402.0),
    ]
    rejected, detail = _rejected(
        "duplicate timestamp",
        lambda: CandidateInputs(composition={}, process={}, heat_pattern=duplicated),
    )
    results.append(
        (
            "timestamp重複を値を残したまま不適格として保持できる",
            not rejected,
            f"{detail}（保存自体が拒否されるため品質findingとして残せない）",
        )
    )

    # 6. CanonicalCandidate に系列の正規化provenanceを置けるか
    rejected, detail = _rejected(
        "canonical provenance",
        lambda: CanonicalCandidate(
            schema_version="canonical-candidate/v1",
            task_id="spike",
            composition={},
            process={},
            heat_pattern=None,
            categorical={},
            provenance={"source_kind": "direct", "source_ref": None},  # type: ignore[arg-type]
            series_provenance={"source_unit": "°F", "conversion_id": "f-to-c"},  # type: ignore[call-arg]
        ),
    )
    results.append(
        ("CanonicalCandidateに系列の正規化provenanceを置ける", not rejected, detail)
    )

    # 7. Chain Stageとして系列入力Taskを使えるか
    from material_workbench.contracts.chain_contracts import task_contract_surface

    series_task = TaskDefinition(
        schema_version="task-definition/v1",
        id="spike-series-task-v1",
        label="スパイク：系列入力Task",
        canonical_candidate_schema_version="canonical-candidate/v1",
        input_groups=(
            InputGroupDefinition(
                key="process",
                order=0,
                label="工程",
                fields=(
                    InputFieldDefinition(
                        path="process.ls_mpm",
                        kind="number",
                        order=0,
                        label="ライン速度",
                        unit="m/min",
                        default_range=NumericRange(min=80, max=120),
                        allowed_range=NumericRange(min=10, max=200),
                        training_range=NumericRange(min=85, max=115),
                    ),
                ),
            ),
            InputGroupDefinition(
                key="heat_pattern",
                order=1,
                label="温度履歴",
                fields=(
                    InputFieldDefinition(
                        path="heat_pattern", kind="heat_pattern", order=0, label="温度履歴"
                    ),
                ),
            ),
        ),
        outputs=(
            {
                "key": "grain_size_um",
                "label": "結晶粒径",
                "unit": "µm",
                "goal_direction": "at_most",
                "measurement_keys": ("grain_size_um",),
                "plausibility_range": NumericRange(min=0, max=500),
                "preferred_display_range": NumericRange(min=1, max=100),
            },  # type: ignore[arg-type]
        ),
        display_decimals={"process.ls_mpm": 1, "output.grain_size_um": 2},
    )
    rejected, detail = _rejected(
        "chain series stage",
        lambda: task_contract_surface(series_task, contract_digest="sha256:" + "0" * 64),
    )
    results.append(("系列入力TaskをChain Stageにできる", not rejected, detail))

    print()
    for label, ok, detail in results:
        print(f"[{'OK  ' if ok else 'NG  '}] {label}" + (f"\n        -> {detail}" if not ok else ""))

    blocked = [label for label, ok, _ in results if not ok]
    print("\n=== 結論 ===")
    print(f"現行契約で表現できない項目: {len(blocked)}/{len(results)}")
    for label in blocked:
        print(f"- {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
