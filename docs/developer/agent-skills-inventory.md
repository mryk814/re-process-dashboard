# Agent Skills inventory

最終review: 2026-08-08

この文書は、repo-scoped Agent Skillsのsource、固定版、安全境界、更新方法の正本です。
外部Skillは第三者コードと同じ依存として扱います。skills.shのinstall数やrankingは
候補発見にだけ使い、採用根拠にはしません。

## 配置と検出

Codex公式仕様は、repository Skillを`.agents/skills/<name>/SKILL.md`から検出し、
`$skill-name`で明示呼び出しできると定めています。Skillのnameが重複してもmergeされないため、
検出対象とrepo内のinternal reference、vendor原典を分離します。

```text
.agents/skills/                 Codexが検出するpublic repo wrapper（6件）
.agents/references/skills/      inventoryが管理するinternal wrapper（非検出）
.agents/vendor/                 commit固定した外部原典。CodexのSkill root外
.claude/skills/                 既存repo固有Skillの単一正本
.agents/vendor/external-skills.lock.json
                                source commitと主要SHA-256
```

Windowsのsymlink／junctionには依存しません。`data-contributor`と
`scenario-journey-evaluator`は既存`.claude/skills`を単一正本とし、`.agents/skills`側は
原典を全文読む薄いwrapperです。外部Skillも原典本文を改変せずvendorし、安全制限を
repo wrapperへ置きます。`.agents/references/skills`はCodexの検出root外に置き、
public wrapperから相対参照します。clientごとのroot外reference対応はinventoryで
`unknown`と記録し、未観測の対応を推測しません。

Codexの公式根拠:

- [Build skills — Where Codex loads local skills](https://learn.chatgpt.com/docs/build-skills)
- [openai/codex skills documentation pointer](https://github.com/openai/codex/blob/main/docs/skills.md)

## Typed inventoryとvisible boundary

`.agents/skill-inventory.json`が、public entryとinternal referenceを分ける
`skill-inventory/v1`のmachine-readable authorityです。`scripts/check-skill-inventory.mjs`
はinventory、wrapper、lock、filesystemをread-onlyで照合します。

2026-08-08にbase commit `f48bdb469e71e4275ab774fd430f490122c892ee`で観測した
historical baselineは12件でした。移動後は`.agents/skills`直下のpublic entryが次の6件、
strict targetも6件です。

```text
data-contributor
frontend-ux-architect
re-process-architecture-review
scenario-journey-evaluator
systematic-debugging
verification-budget-planner
```

6件の旧外部Skill wrapperは`.agents/references/skills/`へ物理移動し、
`agents/openai.yaml`を持たせていません。vendor原典と`.claude/skills`のcanonical
sourceは変更していません。移行表は旧 `$skill-name`を次のpublic入口へ明示誘導します。

| 旧名 | 移行先public entry | 状態 |
|---|---|---|
| `codebase-design` | `re-process-architecture-review` | active |
| `domain-modeling` | `re-process-architecture-review` | active |
| `improve-codebase-architecture` | `re-process-architecture-review` | active |
| `web-design-guidelines` | `frontend-ux-architect` | active |
| `vercel-react-best-practices` | `frontend-ux-architect` | active |
| `vercel-composition-patterns` | `frontend-ux-architect` | active |

checkerはpublic wrapperからinternal referenceへの到達、broken link、root外への相対path、
symlink、duplicate、recursive link、public/internalの型境界、pinned commit／license／
SHA-256とlock driftを検査します。vendor script、network、write、automatic executionは
実行せず、inventoryの安全フラグでも禁止します。

```powershell
node scripts/check-skill-inventory.mjs --strict-target --json
node --test scripts/check-skill-inventory.test.mjs
```

checkerのJSON reportには`inventory_id`、`inventory_digest`、`observed_commit`、
`discovery.baseline_count`、`discovery.observed_count`、`discovery.target_count`と
typed entry countが含まれます。これは#838が後続の代表task比較でbounded evidenceとして
消費できるidentity/countであり、#839では代表taskの迷い・token量比較自体は実施しません。
Codexのfilesystem観測以外のChatGPT／Claude clientのresolution、version、root外reference
対応は`unknown`であり、unsupportedとは扱いません。

## 導入した外部Skill

| Skill | Source / pinned commit | 更新状況・license | skills.sh | vendor / wrapper | scripts・network・変更権限 | repository制限 |
|---|---|---|---|---|---|---|
| `codebase-design` | [mattpocock/skills](https://github.com/mattpocock/skills) `2ab958093e83e0ec752e6c1c5932da465bf23e0c` | SKILL 2026-06-17、repoは2026-07も更新、MIT | [page](https://www.skills.sh/mattpocock/skills/codebase-design) | `.agents/vendor/mattpocock-skills/codebase-design` / `.agents/references/skills/codebase-design` | script・networkなし。補助文書にsubagentと旧test削除の手順あり | 明示呼び出しのみ。authority、atomicity、実在するsecond adapterを先に確認し、testを機械削除しない |
| `domain-modeling` | 同上 | SKILL 2026-06-17、MIT | [page](https://www.skills.sh/mattpocock/skills/domain-modeling) | `.agents/vendor/mattpocock-skills/domain-modeling` / `.agents/references/skills/domain-modeling` | command・networkなし。`CONTEXT.md`とADRを書きうる | 明示呼び出しのみ。repoのauthority docsを使い、明示依頼なしに`CONTEXT.md`／ADRを作らない |
| `improve-codebase-architecture` | 同上 | SKILL 2026-07-13、MIT | [page](https://www.skills.sh/mattpocock/skills/improve-codebase-architecture) | `.agents/vendor/mattpocock-skills/improve-codebase-architecture` / `.agents/references/skills/improve-codebase-architecture` | `git log`、temp HTML、browser open。HTMLはfloating Tailwind／Mermaid CDNと`securityLevel: loose`を使用。`grilling`等へ依存 | 明示呼び出しのみ。HTML／CDN／browser openを禁止しMarkdownへ置換。Issue・ADR・code changeは依頼時だけ |
| `systematic-debugging` | [obra/superpowers](https://github.com/obra/superpowers) `44c9b2d6e889982ac18c27d05a19fefe335194e1` | SKILL 2026-07-24、release v6.2.0、MIT | [page](https://www.skills.sh/obra/superpowers/systematic-debugging) | `.agents/vendor/obra-superpowers/systematic-debugging` / `.agents/skills/systematic-debugging` | `find-polluter.sh`はBash/npm testを反復実行。本文にmacOS signing例あり。自動network／deleteなし。upstreamのTDD／verification Skill依存は未導入 | script・署名例・任意shellを自動実行しない。未導入依存はrepoのfocused failing testとverification policyへ置換。UI/API/persistence/environment/fixtureを分離し、retry／fallbackで隠さない |
| `web-design-guidelines` | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) `7c180d9044c9ae2b442b567aad4e42a28dd5ed62` | SKILL 2026-01-16、repoは2026-07も更新、MIT宣言 | [page](https://www.skills.sh/vercel-labs/agent-skills/web-design-guidelines) | `.agents/vendor/vercel-agent-skills/web-design-guidelines` / `.agents/references/skills/web-design-guidelines` | 原典は実行時にmutable `main`をfetch | 明示呼び出しのみ。fetchを禁止し、`web-interface-guidelines` `4e799d45c17aec1498c269287a83b9dba22b966b`のlocal snapshotを使う。視覚正本はrepo design system |
| `vercel-react-best-practices` | 同上 | SKILL 2026-04-14、MIT | [page](https://www.skills.sh/vercel-labs/agent-skills/react-best-practices) | `.agents/vendor/vercel-agent-skills/react-best-practices` / `.agents/references/skills/vercel-react-best-practices` | Markdown rulesのみ。追加library名は例であり自動installしない | 明示呼び出しのみ。React client/Vite規則だけを使い、Next.js/RSC/Server Actions規則を除外。計測前に最適化しない |
| `vercel-composition-patterns` | 同上 | SKILL 2026-01-28、MIT | [page](https://www.skills.sh/vercel-labs/agent-skills/composition-patterns) | `.agents/vendor/vercel-agent-skills/composition-patterns` / `.agents/references/skills/vercel-composition-patterns` | Markdown rulesのみ、React 19節あり | 明示呼び出しのみ。UX問題をcomponent APIへ縮小せず、ownerを曖昧にせず、boolean削減だけのframeworkを作らない |

skills.shの各原典ページに`Originally from`表示はありません。
`web-design-guidelines`がruntimeで参照する規則の原典は
[vercel-labs/web-interface-guidelines](https://github.com/vercel-labs/web-interface-guidelines)です。
source path、SHA-256、取得できるlicense fileまたはlicense宣言の証拠は
[`external-skills.lock.json`](../../.agents/vendor/external-skills.lock.json)に固定しています。
Vercel Agent Skillsの監査commitにはroot LICENSEがなく、READMEの`MIT`宣言だけが原典の
license evidenceです。そのREADMEを`UPSTREAM-README.md`として固定していますが、
完全なlicense textが原典にない点は、repository外へ再配布する前に再確認が必要です。

vendored `SKILL.md`は無改変です。docs link checkを実際に通すため、upstream参照文書には
2種類の限定patchがあります。Domain Modeling templateの存在しない例示pathをcode表記へ変え、
Vercel生成済み`AGENTS.md`の3 linkへ欠けていた`rules/`を補いました。更新時は再適用せず、
upstreamで解消済みかを先に確認します。

## repo固有Skill

| Skill | 正本 / 検出wrapper | implicit trigger | 明示例 |
|---|---|---|---|
| `re-process-architecture-review` | `.agents/skills/re-process-architecture-review` → `.agents/references/skills/{codebase-design,domain-modeling,improve-codebase-architecture}` | architecture audit、責務境界、package、registry、adapter、authority、migration。typoや局所CSSでは発火させない | `$re-process-architecture-review store_unit_of_work.pyをaudit-onlyで確認して` |
| `frontend-ux-architect` | `.agents/skills/frontend-ux-architect` → `.agents/references/skills/{web-design-guidelines,vercel-react-best-practices,vercel-composition-patterns}` | 画面構造、navigation、onboarding、form、Workbench、結果配置、handoff。token置換だけでは発火させない | `$frontend-ux-architect Data Libraryの構造を実装なしで監査して` |
| `data-contributor` | `.claude/skills/data-contributor` / `.agents/skills/data-contributor` | 外部Excel／CSVのUI onboarding | `$data-contributor このExcelをData Libraryから接続して` |
| `scenario-journey-evaluator` | `.claude/skills/scenario-journey-evaluator` / `.agents/skills/scenario-journey-evaluator` | prediction-ready Projectの判断journey | `$scenario-journey-evaluator handoff済みProjectを実画面で評価して` |
| `systematic-debugging` | pinned vendor / `.agents/skills/systematic-debugging` | bug、test failure、unexpected behavior | `$systematic-debugging このfixture failureを原因調査だけして` |

architecture reviewではconfirmed observation、impact、current authority、existing protection、
proposal、alternative、risk、migration／compatibility、verification、no-changeを分けます。
frontend reviewでは実装前に利用者の問い、確定済み情報、今回決めること、working memory、
再入力、往復、scroll、復旧、削除候補、異なる構造案、配置理由、実画面受入を記録します。

## 標準導入を見送った候補

| Candidate | Reviewed source | 判断 |
|---|---|---|
| Anthropic `frontend-design` | [anthropics/skills](https://github.com/anthropics/skills/tree/b29e7cf65e5cb78a5ac33d582270551bc74a14eb/skills/frontend-design)、Apache-2.0、script／networkなし | 実行riskは低いが、強いvisual identity、font、signature、motionが科学的判断UIの密度・既存design systemを上書きしうる。標準triggerにはせず、隔離したUI Labで明示的に比較する候補 |
| `impeccable` | [pbakaus/impeccable](https://github.com/pbakaus/impeccable/tree/c5e1ddd054dc093ef2546c36b82eddf2c4e84bb9)、v4.0.4、Apache-2.0 | update network、HOME配下write、hooks、child process、browser server、画像API、source writeを含む大きな権限面を持つ。標準導入しない。将来はread-only audit subsetだけを別途監査する |
| 強いbrowser／大量refactor／arbitrary shell Skill | 未指定 | repositoryの既存Playwright、sandbox、verification、実データ保護を迂回するため導入しない |

## 更新方法

1. GitHub原典の最新commit、path、`SKILL.md`、license、最終更新、archive状態、releaseを確認する。
2. `scripts/`、command例、network、delete／write、sandbox、他Skill依存、`Originally from`を再監査する。
3. `$skill-installer`の
   `install-skill-from-github.py --repo <owner/repo> --ref <full-commit> --dest .agents/vendor-update/<source> --path <paths...>`
   で一時stagingへ取得する。既存vendorへ直接上書きしない。
4. 現行vendorとの差分を全文reviewし、wrapper制限とlicenseを更新する。
5. review済みstagingでvendorを置換し、`external-skills.lock.json`のcommitとSHA-256を更新する。
6. Skill validation、duplicate/dependency/link check、dry-run、`verify:pr`を実行する。
7. PRにsource commit、security差分、見送った変更、未実行の外部証拠を記録する。

外部Skillのscriptは更新検査やdry-runで実行しません。新しいnetwork／write／deleteが増えた場合は、
wrapperで禁止できるか、導入を見送るべきかを先に判断します。

## Dry-run記録

2026-08-01にfresh subagentで実行しました。3 SkillともCodexのAvailable Skills catalogへ
repo path付きで表示され、明示pathと同じ実体を読みました。コード変更、Issue／RFC／ADR作成、
external report、vendored script実行はありません。

### Architecture

対象は`backend/src/decision_workbench/persistence/store_unit_of_work.py`です。

- 行数だけの分割を拒否し、current baseline、transaction authority、dependency tests、
  migration policyを先に確認した
- confirmed observation、impact、authority、protection、proposal、alternative、risk、
  migration、verification、no-changeを分離した
- module全体はatomic cross-aggregate command ownerとして維持するno-change判断を出した
- 併せて、Chain Project初期候補のCandidate Revision保存欠落と、AI review競合経路の
  別connection読込という2件の既存実装所見を報告した

所見はこのSkill導入PRで修正せず、指示どおり新規Issueにもしていません。

### Frontend

対象は`apps/web/src/features/data-library/DataLibraryPage.tsx`と関連Data Library flowです。

- browse、add、model preparation、comparison／series、Source Lifecycleという複数の問い、
  暗黙選択、working memory、scroll、URLへ残らないstate、error recoveryを数えた
- CSS変更に縮小せず、「目的別route＋Dataset detail」と「永続master-detail workspace」
  という認知モデルの異なる2案を比較した
- Dataset／Task／Package／Revision等のscientific identityを隠さない受入観察を定義した
- 実画面は起動せず、fresh UIでのscroll、small viewport、keyboard、resumeは未反証と明記した

### Debugging

対象は`e2e/fixtures/broken-active-transforms.json`と専用degraded経路です。

- fixtureの`not-json`を実loaderで再現し、JSON validationからTransform unavailable、
  依存Chain unavailableへ至る境界を追跡した
- 意図的fixtureなのでapplication fixは不要と判断し、実環境なら正本catalog修復が
  最小候補であること、retry／fallbackは不要であることを示した
- focused regression
  `test_broken_transform_disables_only_dependent_chain`と
  `test_broken_transform_preserves_saved_chain_inputs_read_only`は2件passした
- 専用Playwright configは305.8秒でtimeoutし、完了証拠は得られなかった

これらはproxyによるwrapper挙動のforward-testです。architecture所見、UX案、実読者の理解、
実画面の使いやすさを単独承認するものではありません。
