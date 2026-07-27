import { expect, test, type Page } from "@playwright/test";

import { expectNoBlockingAxeViolations, expectNoUndersizedText } from "./axe";
import { apiBaseUrl, createProjectWithBinding } from "./helpers";

type Surface = {
  name: string;
  url: string;
  ready: (page: Page) => Promise<void>;
  prepare?: (page: Page) => Promise<void>;
};

const heading = (
  name: string | RegExp,
  level?: 1 | 2 | 3 | 4 | 5 | 6,
) => async (page: Page) => {
  await expect(page.getByRole("heading", { name, level }).first()).toBeVisible();
};

const settings = [
  ["開発者ガイド", "developer", /構成・変更判断・診断/],
  ["データ品質集計", "quality", "データ品質集計"],
  ["入力範囲", "ranges", "入力範囲設定"],
  ["表示桁数", "display", "表示桁数"],
  ["予測タスク定義", "task", "予測タスク定義"],
  ["モデルと実行環境", "model", "モデルと実行環境"],
] as const;

const surfaces: Surface[] = [
  {
    name: "プロジェクト概要",
    url: "/?view=project&project=default",
    ready: heading("焼鈍条件の候補検討", 1),
  },
  {
    name: "候補比較",
    url: "/?view=candidates&project=default",
    ready: heading(/候補比較表/),
  },
  {
    name: "データライブラリ",
    url: "/?view=data-library",
    ready: heading("データライブラリ"),
  },
  {
    name: "範囲探索",
    url: "/?view=explore&project=default",
    ready: heading("範囲探索"),
  },
  {
    name: "工程系譜",
    url: "/?view=lineage&project=default",
    ready: async (page) => {
      await expect(page.getByRole("complementary", { name: "系譜ノード検索" })).toBeVisible();
      await expect(page.locator(".lineage-source-facts")).toBeVisible();
    },
  },
  {
    name: "品質",
    url: "/?view=quality&project=default",
    ready: heading("問題から探す"),
  },
  ...settings.map(([name, section, ready]) => ({
    name: `開発・管理: ${name}`,
    url: `/?view=settings&project=default&admin=${section}`,
    ready: heading(ready),
  })),
  {
    name: "検討アクティビティ",
    url: "/?view=candidates&project=default",
    prepare: async (page) => {
      await page.getByRole("button", { name: "検討アクティビティ" }).click();
    },
    ready: heading("ロバストネス／公差解析"),
  },
];

for (const surface of surfaces) {
  test(`${surface.name}の表示をアクセシビリティ検査できる`, async ({ page }) => {
    await page.goto(surface.url);
    if (surface.prepare) await surface.prepare(page);
    await surface.ready(page);

    await expectNoBlockingAxeViolations(page, surface.name);
    await expectNoUndersizedText(page);
  });
}

test("Chain候補編集面をアクセシビリティ検査できる", async ({
  page,
  request,
}) => {
  const chainsResponse = await request.get(`${apiBaseUrl}/api/chains`);
  expect(chainsResponse.status(), await chainsResponse.text()).toBe(200);
  const chains = await chainsResponse.json() as Array<{
    definition: { chain_id: string };
    revisions: Array<{ revision: number; revision_digest: string }>;
  }>;
  const chain = chains.find(
    (item) => item.definition.chain_id === "welding-consumable-a-b-c-v1",
  );
  expect(chain).toBeTruthy();
  const revision = chain!.revisions[0];
  const projectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: `axe Chain ${Date.now()}`,
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${chain!.definition.chain_id}:r${revision.revision}`,
        chain_revision_digest: revision.revision_digest,
      },
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const contractResponse = await request.get(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidate-contract`,
  );
  expect(contractResponse.status(), await contractResponse.text()).toBe(200);
  const contract = await contractResponse.json() as { starter_candidate: object };
  const candidateResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates`,
    { data: contract.starter_candidate },
  );
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);

  await page.goto(`/?view=candidates&project=${project.id}`);
  await expect(page.getByRole("region", { name: "Chain候補作業面" })).toBeVisible();
  await expectNoBlockingAxeViolations(page, "Chain候補編集面");
  await expectNoUndersizedText(page);
});

test("候補が空の状態をアクセシビリティ検査できる", async ({
  page,
  request,
}) => {
  const project = await createProjectWithBinding(
    request,
    "annealed-properties-v1",
    `axe empty ${Date.now()}`,
  );
  await page.goto(`/?view=candidates&project=${project.id}`);
  await expect(page.getByRole("heading", { name: "候補を表示できません" })).toBeVisible();
  await expect(page.getByRole("button", { name: "最初の候補を作る" })).toBeVisible();
  await expectNoBlockingAxeViolations(page, "候補が空の状態");
});
