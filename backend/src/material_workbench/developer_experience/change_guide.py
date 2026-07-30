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
            command("uv", ["run", "python", "backend/scripts/operations/profile_workbench.py", "inspect", "path/to/file.xlsx"]),
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
            command(
                "npm",
                [
                    "run",
                    "verify:edit",
                    "--",
                    "backend/tests/test_task_registry.py",
                    "backend/tests/test_feature_pipeline.py",
                ],
            ),
        ],
        documents=["docs/recipes/add-input-field.md", "docs/contracts/feature-engineering.md"],
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
            command(
                "npm",
                [
                    "run",
                    "verify:edit",
                    "--",
                    "backend/tests/test_task_contracts.py",
                    "backend/tests/test_model_lifecycle.py",
                ],
            ),
        ],
        documents=["docs/contracts/model-package-contract.md"],
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
            command(
                "npm",
                ["run", "verify:edit", "--", "backend/tests/test_feature_pipeline.py"],
            ),
        ],
        documents=["docs/contracts/feature-engineering.md"],
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
        documents=["docs/operations/model-package-lifecycle.md", ".claude/skills/add-model-runtime/SKILL.md"],
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
            command(
                "npm",
                [
                    "run",
                    "verify:edit",
                    "--",
                    "backend/tests/test_task_registry.py",
                    "backend/tests/test_openapi_contract.py",
                ],
            ),
        ],
        documents=[".claude/skills/add-prediction-task/SKILL.md"],
        human_review="新Taskか既存Taskのversion更新かを人が判断します。",
    ),
    ChangeGuideEntry(
        id="decision-activity-new",
        label="新しいDecision Activityを追加したい",
        risk="specialist",
        changes=[
            "parameter / result contract",
            "Activity registry",
            "application service / handler",
            "FastAPI route",
            "React Activity View",
            "contract / UI / E2E test",
        ],
        unchanged=["TaskDefinition", "既存Activity contract", "保存済みActivity Run"],
        artifacts=["新しいActivity version", "OpenAPI", "generated API types", "frontend build"],
        steps=[
            {
                "label": "1. Python contract",
                "paths": [
                    "backend/src/material_workbench/contracts/decision_activity_contracts.py",
                ],
                "outcome": "parameterとresultを別々のschema_versionを持つ型として定義する",
            },
            {
                "label": "2. Registry",
                "paths": [
                    "backend/src/material_workbench/application/decision_activity_registry.py",
                ],
                "outcome": "Activity definitionと利用条件を一つのregistryへ登録する",
            },
            {
                "label": "3. Application",
                "paths": [
                    "backend/src/material_workbench/application/decision_activities.py",
                ],
                "outcome": "handler、provenance、immutable Runの保存を実装する",
            },
            {
                "label": "4. API",
                "paths": [
                    "backend/src/material_workbench/api/decision_activities.py",
                ],
                "outcome": "共通routeから型付きrequestとresponseを公開する",
            },
            {
                "label": "5. Generated contract",
                "paths": [
                    "apps/web/src/generated/openapi.json",
                    "apps/web/src/generated/api-types.ts",
                ],
                "outcome": "api:generateでOpenAPIとTypeScript型を再生成する",
            },
            {
                "label": "6. React View",
                "paths": [
                    "apps/web/src/features/workbench/decisionActivities/registry.ts",
                    "apps/web/src/features/workbench/decisionActivities/",
                    "apps/web/src/features/workbench/DecisionActivityPanel.tsx",
                ],
                "outcome": "resultのdiscriminatorに対応する表示と操作を登録する",
            },
            {
                "label": "7. Contract / UI / E2E test",
                "paths": [
                    "backend/tests/test_decision_activities.py",
                    "backend/tests/test_openapi_contract.py",
                    "apps/web/tests/activityRunNavigation.test.mjs",
                    "e2e/decision-activity.spec.ts",
                ],
                "outcome": "契約、保存identity、画面遷移、利用者経路を順に検証する",
            },
        ],
        warnings=[
            "apps/web/src/generated/openapi.jsonとapi-types.tsは直接編集せず、npm run api:generateで再生成します。",
            "既存Activityの型を流用せず、新しいparameterとresultのschema_versionを定義します。",
        ],
        commands=[
            command("npm", ["run", "api:generate"]),
            command(
                "npm",
                [
                    "run",
                    "verify:edit",
                    "--",
                    "backend/tests/test_decision_activities.py",
                    "backend/tests/test_openapi_contract.py",
                ],
            ),
            command(
                "node",
                ["--test", "apps/web/tests/activityRunNavigation.test.mjs"],
            ),
            command(
                "npx",
                ["playwright", "test", "e2e/decision-activity.spec.ts"],
            ),
            command("npm", ["run", "build"]),
        ],
        documents=[
            "docs/contracts/decision-activities.md",
            "docs/learning/chapters/contract-through-stack.qmd",
            "docs/developer-start-here.md",
        ],
        human_review="新しいActivityか既存Activityの拡張か、保存済みRunと意味を共有できるかを人が判断します。",
    ),
    ChangeGuideEntry(
        id="decision-activity-change",
        label="既存Decision Activityを変更したい",
        risk="review",
        changes=[
            "対象Activityのparameter / result contract",
            "Activity definition / handler",
            "FastAPI contract",
            "React Activity View",
            "既存Runの互換性test",
        ],
        unchanged=["無関係なActivity", "TaskDefinition", "保存済みActivity Runの内容"],
        artifacts=["互換性を保てない場合は新しいActivity version", "OpenAPI", "generated API types"],
        steps=[
            {
                "label": "1. Python contract",
                "paths": [
                    "backend/src/material_workbench/contracts/decision_activity_contracts.py",
                ],
                "outcome": "shapeまたは意味が変わる場合はschema_versionを分ける",
            },
            {
                "label": "2. Registry",
                "paths": [
                    "backend/src/material_workbench/application/decision_activity_registry.py",
                ],
                "outcome": "availability、既定parameter、表示metadataを更新する",
            },
            {
                "label": "3. Application",
                "paths": [
                    "backend/src/material_workbench/application/decision_activities.py",
                ],
                "outcome": "handlerとprovenanceを更新し、保存済みRunを自動再計算しない",
            },
            {
                "label": "4. API",
                "paths": [
                    "backend/src/material_workbench/api/decision_activities.py",
                ],
                "outcome": "errorとresponseの契約変更をOpenAPIへ反映する",
            },
            {
                "label": "5. Generated contract",
                "paths": [
                    "apps/web/src/generated/openapi.json",
                    "apps/web/src/generated/api-types.ts",
                ],
                "outcome": "api:generateでOpenAPIとTypeScript型を再生成する",
            },
            {
                "label": "6. React View",
                "paths": [
                    "apps/web/src/features/workbench/decisionActivities/registry.ts",
                    "apps/web/src/features/workbench/decisionActivities/",
                    "apps/web/src/features/workbench/DecisionActivityPanel.tsx",
                ],
                "outcome": "変更後のparameterとresultをdiscriminatorで描画する",
            },
            {
                "label": "7. Contract / UI / E2E test",
                "paths": [
                    "backend/tests/test_decision_activities.py",
                    "backend/tests/test_openapi_contract.py",
                    "apps/web/tests/activityRunNavigation.test.mjs",
                    "e2e/decision-activity.spec.ts",
                ],
                "outcome": "旧Run読込、現行契約、stale response、主要画面経路を検証する",
            },
        ],
        warnings=[
            "apps/web/src/generated/openapi.jsonとapi-types.tsは直接編集せず、npm run api:generateで再生成します。",
            "保存済みRunは変更後のhandlerで自動再計算せず、作成時のprovenanceと結果を保持します。",
        ],
        commands=[
            command("npm", ["run", "api:generate"]),
            command(
                "npm",
                [
                    "run",
                    "verify:edit",
                    "--",
                    "backend/tests/test_decision_activities.py",
                    "backend/tests/test_openapi_contract.py",
                ],
            ),
            command(
                "node",
                ["--test", "apps/web/tests/activityRunNavigation.test.mjs"],
            ),
            command(
                "npx",
                ["playwright", "test", "e2e/decision-activity.spec.ts"],
            ),
            command("npm", ["run", "build"]),
        ],
        documents=[
            "docs/contracts/decision-activities.md",
            "docs/learning/chapters/contract-through-stack.qmd",
            "docs/developer-start-here.md",
        ],
        human_review="parameterまたはresultの意味が変わる場合は、既存versionの上書きではなく新versionとして扱います。",
    ),
    ChangeGuideEntry(
        id="presentation",
        label="表示だけ変えたい",
        risk="safe",
        changes=["apps/webのpresentation / CSS"],
        unchanged=["Dataset / Profile / Task / Package"],
        artifacts=["frontend build"],
        commands=[command("npm", ["run", "typecheck"]), command("npm", ["run", "build"])],
        documents=["docs/product/design-system.md"],
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
