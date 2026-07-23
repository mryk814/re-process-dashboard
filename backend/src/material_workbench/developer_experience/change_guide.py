from __future__ import annotations

from material_workbench.developer_experience.commands import developer_command as command
from material_workbench.developer_experience.schemas import ChangeGuideEntry


CHANGE_GUIDE = (
    ChangeGuideEntry(
        id="new-excel",
        label="新しいExcelを使いたい",
        risk="safe",
        changes=["Data Asset / Dataset Revision", "必要ならDataset Input Profile"],
        unchanged=["TaskDefinition", "Feature Pipeline"],
        artifacts=["Dataset Revision", "学習し直す場合はModel Package"],
        commands=[
            command("npm", ["run", "dev:doctor", "--", "--source", "path/to/file.xlsx"]),
            command("uv", ["run", "python", "backend/scripts/profile_workbench.py", "inspect", "path/to/file.xlsx"]),
        ],
        documents=["docs/recipes/add-more-rows.md", "docs/recipes/add-similar-workbook.md"],
    ),
    ChangeGuideEntry(
        id="workbook-shape",
        label="列名やシート名が違う",
        risk="review",
        changes=["Dataset Input Profileのrole / column mapping"],
        unchanged=["TaskDefinition", "Feature Pipeline", "canonical unit"],
        artifacts=["Profile Revision", "Dataset Revision"],
        commands=[command("npm", ["run", "dev:doctor", "--", "--source", "path/to/file.xlsx"])],
        documents=["docs/recipes/add-similar-workbook.md"],
    ),
    ChangeGuideEntry(
        id="input",
        label="入力項目を増やしたい",
        risk="specialist",
        changes=["TaskDefinition", "Feature Pipeline", "Package builder", "TaskModule capability"],
        unchanged=["既存Project / Snapshot"],
        artifacts=["新しい契約", "新しいModel Package", "generated API types"],
        commands=[
            command("npm", ["run", "api:generate"]),
            command("npm", ["run", "task:inventory"]),
            command("npm", ["run", "verify:focused"]),
        ],
        documents=["docs/recipes/add-input-field.md", "docs/feature-engineering.md"],
        human_review="入力の科学的意味と学習単位を人が決めます。",
    ),
    ChangeGuideEntry(
        id="output",
        label="出力特性を増やしたい",
        risk="specialist",
        changes=["TaskDefinition", "Runtime", "Package", "UI presentation"],
        unchanged=["保存済みSnapshot"],
        artifacts=["新しい契約", "新しいModel Package"],
        commands=[
            command("npm", ["run", "api:generate"]),
            command("npm", ["run", "model:verify"]),
            command("npm", ["run", "verify:focused"]),
        ],
        documents=["docs/model-package-contract.md"],
        human_review="目的変数と品質基準を人が決めます。",
    ),
    ChangeGuideEntry(
        id="features",
        label="特徴量を変えたい",
        risk="specialist",
        changes=["Feature Pipeline", "golden test", "Package builder"],
        unchanged=["元Excel", "保存済みProject / Snapshot"],
        artifacts=["新しいFeature Pipeline version", "新しいModel Package"],
        commands=[
            command("npm", ["run", "model:build"]),
            command("npm", ["run", "model:verify"]),
            command("npm", ["run", "verify:focused"]),
        ],
        documents=["docs/feature-engineering.md"],
        human_review="特徴量の妥当性と反復観測の扱いを人がレビューします。",
    ),
    ChangeGuideEntry(
        id="model",
        label="モデルを変えたい",
        risk="review",
        changes=["Package builder", "allow-list済みadapter（必要な場合）"],
        unchanged=["Dataset Profile", "TaskDefinition"],
        artifacts=["新しいModel Package"],
        commands=[command("npm", ["run", "model:build"]), command("npm", ["run", "model:verify"])],
        documents=["docs/model-package-lifecycle.md", ".claude/skills/add-model-runtime/SKILL.md"],
    ),
    ChangeGuideEntry(
        id="task",
        label="新しい予測Taskを追加したい",
        risk="specialist",
        changes=["TaskDefinition", "Profile", "Feature Pipeline", "Runtime", "TaskModule"],
        unchanged=["既存Task"],
        artifacts=["新Task一式", "active package", "task inventory", "generated API types"],
        commands=[
            command("npm", ["run", "task:inventory"]),
            command("npm", ["run", "api:generate"]),
            command("npm", ["run", "verify:focused"]),
        ],
        documents=[".claude/skills/add-prediction-task/SKILL.md"],
        human_review="新Taskか既存Taskのversion更新かを人が判断します。",
    ),
    ChangeGuideEntry(
        id="presentation",
        label="表示だけ変えたい",
        risk="safe",
        changes=["apps/webのpresentation / CSS"],
        unchanged=["Dataset / Profile / Task / Package"],
        artifacts=["frontend build"],
        commands=[command("npm", ["run", "typecheck"]), command("npm", ["run", "build"])],
        documents=["docs/design-system.md"],
    ),
    ChangeGuideEntry(
        id="unknown",
        label="分からない",
        risk="review",
        changes=["まずDoctorとDeveloper Start Hereで分類"],
        unchanged=["分類前は契約と成果物を変更しない"],
        artifacts=[],
        commands=[
            command("npm", ["run", "dev:doctor"]),
            command("npm", ["run", "dev:doctor", "--", "--source", "path/to/file.xlsx"]),
        ],
        documents=["docs/developer-start-here.md"],
        human_review="目的変数・学習単位・relationが変わるなら専門レビューへ進みます。",
    ),
)


def change_guide_entries() -> list[ChangeGuideEntry]:
    return list(CHANGE_GUIDE)
