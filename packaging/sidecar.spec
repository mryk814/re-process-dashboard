from pathlib import Path
from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH).parent
package_root = root / "backend" / "src" / "material_workbench"
datas = [
    *((str(path), "material_workbench/data") for path in (package_root / "data").glob("*.json")),
    *((str(path), "material_workbench/tasks/task_definitions") for path in (package_root / "tasks" / "task_definitions").glob("*.json")),
]
lightgbm_datas, lightgbm_binaries, lightgbm_hiddenimports = collect_all("lightgbm")
datas.extend(lightgbm_datas)

analysis = Analysis(
    [str(root / "backend" / "src" / "sidecar.py")],
    pathex=[str(root / "backend" / "src")],
    binaries=lightgbm_binaries,
    datas=datas,
    hiddenimports=lightgbm_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="material-workbench-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Electron starts the process with windowsHide=True. A console-capable
    # executable keeps stdout/stderr pipeable without showing a window.
    console=True,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="material-workbench-sidecar",
)
