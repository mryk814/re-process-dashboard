"""Detect active-package switches that bypassed ``set_active_package``.

``models/active-packages.json`` records ``previous`` only when the active
pointer is moved through :func:`set_active_package`.  Editing the file by hand
leaves ``previous`` behind, and ``npm run model:rollback`` then refuses to run.
The checks here compare the recorded history of the file against the packages
that are actually on disk, so a hand edit is reported instead of surfacing much
later as a failed rollback.

``previous: null`` is legitimate for a task that never switched, and also for a
task whose earlier package is no longer a usable rollback target (deleted,
belonging to another task, or built against a superseded input contract).  The
report separates those accepted cases from real drift.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict

from decision_workbench.modeling.model_lifecycle import (
    ACTIVE_PACKAGES_PATH,
    ActivePackagesConfig,
    REPOSITORY_ROOT,
    load_active_packages,
    task_input_contract_digest,
)
from decision_workbench.tasks.task_registry import load_task_contracts


WORKING_TREE_REVISION = "working-tree"

RollbackTargetStatus = Literal["available", "missing", "task-mismatch", "contract-mismatch"]

_STATUS_REASON: dict[RollbackTargetStatus, str] = {
    "available": "現在のTaskDefinitionで検証できるPackageです。",
    "missing": "Packageがmodels/packagesに存在しません。",
    "task-mismatch": "別TaskのPackageです。",
    "contract-mismatch": "入力契約が現在のTaskDefinitionと異なる版です。",
}


def rollback_target_reason(status: RollbackTargetStatus) -> str:
    return _STATUS_REASON[status]


class HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActivePackageChange(HistoryModel):
    """The most recent revision in which one task's active pointer moved."""

    task_id: str
    revision: str
    replaced: str
    active: str
    recorded_previous: str | None
    rollback_target: RollbackTargetStatus
    reason: str


class ActivePackagePreviousIssue(HistoryModel):
    """A recorded ``previous`` that cannot serve as a rollback target."""

    task_id: str
    previous: str
    rollback_target: RollbackTargetStatus
    reason: str


class ActivePackageHistoryReport(HistoryModel):
    schema_version: Literal["active-package-history/v1"] = "active-package-history/v1"
    available: bool
    unavailable_reason: str | None = None
    drift: tuple[ActivePackageChange, ...] = ()
    accepted: tuple[ActivePackageChange, ...] = ()
    broken_previous: tuple[ActivePackagePreviousIssue, ...] = ()
    superseded_previous: tuple[ActivePackagePreviousIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.drift and not self.broken_previous


def classify_rollback_target(
    task_id: str,
    relative: str,
    *,
    models_root: Path,
    contract_digests: dict[str, str],
) -> RollbackTargetStatus:
    """Report whether ``relative`` could be activated for ``task_id`` today."""

    manifest_path = models_root / relative / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "missing"
    if manifest.get("task_id") != task_id:
        return "task-mismatch"
    expected = contract_digests.get(task_id)
    if expected is not None and manifest.get("input_contract_digest") != expected:
        return "contract-mismatch"
    return "available"


def current_task_input_contract_digests() -> dict[str, str]:
    return {
        task_id: task_input_contract_digest(contract.task_definition)
        for task_id, contract in load_task_contracts().items()
    }


def _latest_active_changes(
    revisions: Sequence[tuple[str, ActivePackagesConfig]],
) -> dict[str, tuple[str, str, str, str | None]]:
    """Keep, per task, the newest revision whose active pointer changed."""

    latest: dict[str, tuple[str, str, str, str | None]] = {}
    previous_config: ActivePackagesConfig | None = None
    for revision, config in revisions:
        if previous_config is not None:
            for task_id, selection in config.tasks.items():
                before = previous_config.tasks.get(task_id)
                if before is None or before.active == selection.active:
                    continue
                latest[task_id] = (revision, before.active, selection.active, selection.previous)
        previous_config = config
    return latest


def detect_active_package_drift(
    revisions: Sequence[tuple[str, ActivePackagesConfig]],
    *,
    models_root: Path,
    contract_digests: dict[str, str],
) -> ActivePackageHistoryReport:
    """Classify the newest active-pointer change of every task.

    ``revisions`` runs oldest first.  A change is drift when the package it
    replaced is still a valid rollback target but ``previous`` does not point at
    it — the signature of an edit that bypassed ``set_active_package``.
    """

    drift: list[ActivePackageChange] = []
    accepted: list[ActivePackageChange] = []
    for task_id, (revision, replaced, active, recorded) in sorted(_latest_active_changes(revisions).items()):
        status = classify_rollback_target(
            task_id,
            replaced,
            models_root=models_root,
            contract_digests=contract_digests,
        )
        change = ActivePackageChange(
            task_id=task_id,
            revision=revision,
            replaced=replaced,
            active=active,
            recorded_previous=recorded,
            rollback_target=status,
            reason=_STATUS_REASON[status],
        )
        if recorded == replaced:
            continue
        (drift if status == "available" else accepted).append(change)

    broken: list[ActivePackagePreviousIssue] = []
    superseded: list[ActivePackagePreviousIssue] = []
    if revisions:
        for task_id, selection in sorted(revisions[-1][1].tasks.items()):
            if selection.previous is None:
                continue
            status = classify_rollback_target(
                task_id,
                selection.previous,
                models_root=models_root,
                contract_digests=contract_digests,
            )
            if status == "available":
                continue
            issue = ActivePackagePreviousIssue(
                task_id=task_id,
                previous=selection.previous,
                rollback_target=status,
                reason=_STATUS_REASON[status],
            )
            # 契約移行で置き換えたときは、set_active_packageが記録したpreviousが
            # そのまま旧契約の版を指す。履歴としては正しいのでエラーにしない。
            # 参照先が消えている・別Taskを指しているのは記録自体の誤りとして扱う。
            (superseded if status == "contract-mismatch" else broken).append(issue)

    return ActivePackageHistoryReport(
        available=True,
        drift=tuple(drift),
        accepted=tuple(accepted),
        broken_previous=tuple(broken),
        superseded_previous=tuple(superseded),
    )


def _git_revisions(config_path: Path, *, repository_root: Path) -> list[tuple[str, ActivePackagesConfig]]:
    relative = config_path.resolve().relative_to(repository_root.resolve()).as_posix()
    log = subprocess.run(
        ["git", "log", "--format=%H", "--reverse", "--", relative],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=True,
    )
    revisions: list[tuple[str, ActivePackagesConfig]] = []
    for commit in log.stdout.split():
        blob = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            revisions.append((commit, ActivePackagesConfig.model_validate_json(blob.stdout)))
        except ValueError:
            continue  # a revision predating the current schema cannot be compared
    return revisions


def load_active_package_history(
    *,
    config_path: Path = ACTIVE_PACKAGES_PATH,
    repository_root: Path = REPOSITORY_ROOT,
) -> list[tuple[str, ActivePackagesConfig]] | None:
    """Read every committed revision plus the working tree, or None without git."""

    try:
        revisions = _git_revisions(config_path, repository_root=repository_root)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    working = load_active_packages(config_path)
    if not revisions or revisions[-1][1] != working:
        revisions.append((WORKING_TREE_REVISION, working))
    return revisions


def check_active_package_history(
    *,
    config_path: Path = ACTIVE_PACKAGES_PATH,
    repository_root: Path = REPOSITORY_ROOT,
    models_root: Path | None = None,
) -> ActivePackageHistoryReport:
    revisions = load_active_package_history(config_path=config_path, repository_root=repository_root)
    if revisions is None:
        return ActivePackageHistoryReport(
            available=False,
            unavailable_reason="git履歴を読めないため、active-packages.jsonの直接編集は検査していません。",
        )
    return detect_active_package_drift(
        revisions,
        models_root=models_root or config_path.resolve().parent,
        contract_digests=current_task_input_contract_digests(),
    )


def rollback_target_note(task_id: str, report: ActivePackageHistoryReport) -> str | None:
    """Explain, for a task without a usable ``previous``, what happened to it."""

    for change in report.drift:
        if change.task_id == task_id:
            return (
                f"{change.revision} で active が {change.replaced} から {change.active} へ変わったのに "
                f"previous が記録されていません。切替は npm run model:activate を通してください。"
            )
    for change in report.accepted:
        if change.task_id == task_id:
            return f"直前のactiveは {change.replaced} ですが、rollback対象になりません（{change.reason}）。"
    return None


def format_active_package_history(report: ActivePackageHistoryReport) -> list[str]:
    if not report.available:
        return [f"active-packages.json履歴: 未検査 — {report.unavailable_reason}"]
    lines: list[str] = []
    for change in report.drift:
        lines.append(
            f"[ERROR] {change.task_id}: {change.revision} で active を {change.replaced} から "
            f"{change.active} へ直接変更し、previous={change.recorded_previous} のままです。"
        )
    for issue in report.broken_previous:
        lines.append(
            f"[ERROR] {issue.task_id}: previous={issue.previous} はrollbackできません（{issue.reason}）。"
        )
    for change in report.accepted:
        lines.append(
            f"[OK] {change.task_id}: previous=null は正当です（直前の {change.replaced} は{change.reason}）。"
        )
    for issue in report.superseded_previous:
        lines.append(
            f"[OK] {issue.task_id}: previous={issue.previous} は履歴として残しますが、"
            f"rollback対象にはなりません（{issue.reason}）。"
        )
    if not lines:
        lines.append("[OK] active-packages.jsonのpreviousは履歴と一致しています。")
    return lines
