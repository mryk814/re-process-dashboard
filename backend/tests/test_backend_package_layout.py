from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench"


def test_backend_package_root_only_contains_entry_and_extension_modules() -> None:
    assert {path.name for path in PACKAGE_ROOT.glob("*.py")} == {
        "__init__.py",
        "app.py",
        "task_modules.py",
    }


def test_backend_responsibility_packages_are_present() -> None:
    expected = {
        "adapters",
        "api",
        "application",
        "contracts",
        "data",
        "domain",
        "execution",
        "modeling",
        "persistence",
        "tasks",
    }
    assert expected <= {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
