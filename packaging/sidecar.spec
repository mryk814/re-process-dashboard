from pathlib import Path


root = Path(SPECPATH).parent
package_root = root / "backend" / "src" / "material_workbench"
datas = [
    *((str(path), "material_workbench") for path in package_root.glob("*.json")),
    *((str(path), "material_workbench/task_definitions") for path in (package_root / "task_definitions").glob("*.json")),
]

analysis = Analysis(
    [str(root / "backend" / "src" / "sidecar.py")],
    pathex=[str(root / "backend" / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    console=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="material-workbench-sidecar",
)
