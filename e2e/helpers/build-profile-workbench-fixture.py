from __future__ import annotations

import argparse
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v1.xlsx"


def build(output: Path) -> None:
    workbook = load_workbook(SOURCE)
    try:
        sheet = workbook["熱延"]
        sheet.title = "熱延条件（設備B）"
        headers = [cell.value for cell in sheet[1]]
        sheet.cell(row=1, column=headers.index("均熱温度[℃]") + 1, value="加熱温度[℃]")
        output.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output)
    finally:
        workbook.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
