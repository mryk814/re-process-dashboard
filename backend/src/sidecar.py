from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import uvicorn


def configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def _workspace_paths(arguments: argparse.Namespace) -> tuple[Path, Path]:
    database = Path(arguments.database).expanduser().resolve()
    data_library = Path(
        arguments.data_library or database.parent / "data-library"
    ).expanduser().resolve()
    return database, data_library


def _print_result(value: Any) -> None:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _run_workspace_command(arguments: argparse.Namespace) -> None:
    from decision_workbench.application.workspace_bundle import (
        cancel_workspace_restore,
        commit_workspace_restore,
        create_workspace_backup,
        finalize_workspace_restore,
        prepare_workspace_restore,
        recover_incomplete_workspace_restores,
        rollback_workspace_restore,
    )

    database, data_library = _workspace_paths(arguments)
    command = arguments.workspace_command
    if command == "export":
        _print_result(
            create_workspace_backup(
                database=database,
                data_library_root=data_library,
                destination=arguments.destination,
                app_version=arguments.app_version,
            )
        )
        return
    if command == "prepare":
        from decision_workbench.bootstrap.resources import prepare_app_resources
        from decision_workbench.modeling.transform_catalog import (
            DeterministicTransformCatalogUnavailableError,
            load_deterministic_transform_catalog,
        )

        resources = prepare_app_resources()
        try:
            transform_catalog = load_deterministic_transform_catalog()
        except DeterministicTransformCatalogUnavailableError:
            transform_catalog = None
        _print_result(
            prepare_workspace_restore(
                database=database,
                data_library_root=data_library,
                source=arguments.source,
                task_registry=resources.task_registry,
                transform_catalog=transform_catalog,
            )
        )
        return
    if command == "commit":
        _print_result(
            commit_workspace_restore(
                database=database,
                data_library_root=data_library,
                restore_token=arguments.restore_token,
            )
        )
        return
    if command == "rollback":
        _print_result(
            rollback_workspace_restore(
                database=database,
                data_library_root=data_library,
                restore_token=arguments.restore_token,
            )
        )
        return
    if command == "finalize":
        _print_result(
            finalize_workspace_restore(
                database=database,
                restore_token=arguments.restore_token,
            )
        )
        return
    if command == "cancel":
        _print_result(
            cancel_workspace_restore(
                database=database,
                restore_token=arguments.restore_token,
            )
        )
        return
    if command == "recover":
        _print_result(
            {
                "status": "recovered",
                "restore_tokens": recover_incomplete_workspace_restores(
                    database,
                    data_library,
                ),
            }
        )
        return
    raise RuntimeError(f"Unsupported workspace command: {command}")


def _add_workspace_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        required=True,
        help="Path to the active workbench.db.",
    )
    parser.add_argument(
        "--data-library",
        help="Path to the active Data Library root.",
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decision-workbench-sidecar")
    commands = parser.add_subparsers(dest="command")
    workspace = commands.add_parser(
        "workspace",
        help="Run an offline Workspace backup or restore operation.",
    )
    workspace_commands = workspace.add_subparsers(
        dest="workspace_command",
        required=True,
    )

    export = workspace_commands.add_parser("export")
    _add_workspace_paths(export)
    export.add_argument("--destination", required=True)
    export.add_argument("--app-version", required=True)

    prepare = workspace_commands.add_parser("prepare")
    _add_workspace_paths(prepare)
    prepare.add_argument("--source", required=True)

    for name in ("commit", "rollback", "finalize", "cancel"):
        operation = workspace_commands.add_parser(name)
        _add_workspace_paths(operation)
        operation.add_argument("--restore-token", required=True)

    recover = workspace_commands.add_parser("recover")
    _add_workspace_paths(recover)
    return parser


def _run_api() -> None:
    from decision_workbench.app import app

    uvicorn.run(
        app,
        host=os.getenv("WORKBENCH_API_HOST", "127.0.0.1"),
        port=int(os.environ["WORKBENCH_API_PORT"]),
        log_level="info",
    )


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    configure_standard_streams()
    arguments = _argument_parser().parse_args(argv)
    try:
        if arguments.command == "workspace":
            _run_workspace_command(arguments)
        elif arguments.command is None:
            _run_api()
        else:
            raise RuntimeError(f"Unsupported command: {arguments.command}")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
