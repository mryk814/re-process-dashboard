# UI変更の認知設計・実画面検証プロセス

この文書は、Evidence Decision WorkbenchのUIを変更するときに、見た目やcomponent構造より先に、利用者の認知作業、情報の順序、配置理由、復旧可能性を検討するための正本です。

- 視覚トークン、表、文字、focus、responsive layout等の恒常的な表示規則は [デザインシステム](design-system.md) を参照します。
- 実装後に曖昧な判断課題を実画面で完走し、迷い・判断安全性・復旧性を評価する場合は [Scenario Journey Evaluator](../../.claude/skills/scenario-journey-evaluator/SKILL.md) を使います。
- この文書は、その間にある **実装前の認知設計と、実装後の小さな反証** を担当します。

## 目的

UI変更では、次の状態を「改善」と扱いません。

- 同時に考えさせる事項を減らさず、余白と整列だけを変えた
- 操作順序を変えず、説明文やtooltipを追加した
- 内部概念を残したまま、日本語ラベルを付けた
- 詳細設定をすべて表示したまま、cardやaccordionへ入れた
- typecheck、Playwright、axe、screenshotが通ったため使いやすいと判断した
- 既存componentの配置を前提に、その中で最も実装しやすい案を選んだ

このプロセスの目的は、画面をきれいにすることではありません。

> 利用者がいま答えたい問いを明確にし、考えなくてよいことを減らし、必要な情報を必要な順序で提示し、配置と遷移の理由を説明可能にする。

## 適用範囲

次のいずれかを変更する場合に適用します。

- 新しい画面、主要panel、modal、drawer、wizard step
- navigation、deep link、戻る・進む、再開経路
- formの項目、順序、group、既定値、主操作
- 結果、警告、error、empty、loading、stale stateの位置と優先度
- 一つの画面で同時に扱うTask、Candidate、Run、Snapshot、Dataset等の文脈
- 画面間のhandoff、再入力、重複確認
- 一覧、比較表、graph、editor等の情報構造
- UI上の内部概念を利用者向け概念へ置き換える変更

次の小変更では、独立したUX Change Briefを省略できます。

- typo修正
- 意味と構造を変えない短い文言修正
- 既存design tokenへの置換
- 明らかなfocus、label、scope、aria属性の欠落修正
- 既に承認された構造内の局所的なbug fix

ただし、省略する場合もこの文書とデザインシステムの規則には従います。

## 基本原則

### 1. 画面名ではなく利用者の問いから始める

「Data Libraryを改善する」「設定画面を整理する」では広すぎます。

例:

- 自分の表データから最初の予測Projectを作りたい
- 次に試す候補を3件選びたい
- この予測が学習範囲内か確認したい
- 固定した判断時点を第三者へ説明したい
- 失敗原因を確認し、画面内の操作で復旧したい

一つのviewportまたは一つのstepで複数の問いを扱う場合は、それらを同時に扱う必要を説明します。

### 2. 既存配置を要件として扱わない

現在のcomponent、state owner、API呼び出し位置は実装事実であり、配置の根拠ではありません。

- 「既存panelがここにある」
- 「このcomponentへ追加しやすい」
- 「関連する設定なので同じ画面へ置く」

だけでは配置理由になりません。

既存構造を維持する場合も、利用者の作業順序、比較対象、記憶負荷、復旧経路から妥当性を再確認します。

### 3. 追加より先に削除・統合・後送りを検討する

新しい説明、badge、card、stepを追加する前に、次を確認します。

- この情報は現在の問いに必要か
- 前段で既に確定していないか
- 結果を見た後で十分ではないか
- 技術詳細へ移せないか
- 同じ事実を別の場所でも表示していないか
- 利用不能な機能を表示し続ける必要があるか
- 二つの選択を一つの意図へ統合できないか

### 4. 科学的な証拠は隠さず、読む順序を設計する

認知負荷を下げるために、次を消したり曖昧にしたりしません。

- predictionとactualの区別
- interval、uncertainty、support、extrapolation
- Dataset、Task、Package、Objective、Design Space、Run、Snapshotのidentity
- stale、partial result、failure、fallback
- constraint violation、missing input、unknown capability
- synthetic、public、private、fixture等のprovenance

ただし、digestや内部parameterを主操作と同じ階層へ常時表示する必要はありません。判断に必要な順序で要約し、再現情報・技術詳細から完全な証拠へ到達できるようにします。

### 5. accessibilityは必要条件であり、使いやすさの証明ではない

keyboard、screen reader、focus、contrast、target size、responsive behaviorは必須です。

一方、axe違反がないことは、次を保証しません。

- 利用者が次の操作を予測できる
- 同時に考える事項が少ない
- 画面間の文脈を覚えなくてよい
- 結果と条件を結び付けて理解できる
- 利用不能な操作を実行前に判別できる

## UI変更の進め方

### Step 1: 利用者の問いと終了条件を固定する

最初に次を書きます。

- 誰が、何を知りたい／達成したいか
- この画面へ来た時点で既に確定していること
- この画面で新たに決めること
- この画面で決めさせないこと
- この画面を出る時点で何が保存・確定・未解決か

Task、API、componentではなく、利用者の作業として記述します。

### Step 2: 現在のjourneyを観察する

コードだけで画面構造を判断しません。可能な範囲で実画面を使い、次を記録します。

- 入口と期待した結果
- 実際に押したcontrol
- 操作結果が現れた場所
- 戻る／やり直す方法
- 前画面から覚えていた情報
- 再入力した情報
- 迷った語、選択肢、状態
- errorまたは前提不足を知った時点

実装前の観察では、問題を修正しながらjourneyを続けません。最初の構造的な摩擦をそのまま記録します。

### Step 3: 認知負荷を棚卸しする

これは心理測定値ではなく、設計上の問いを漏らさないためのinventoryです。数だけで合否を決めません。

| 観点 | 確認すること |
|---|---|
| 初見概念 | 初めて理解しないと進めない語・状態はいくつあるか |
| 同時選択 | 同じ時点で比較・決定させる選択はいくつあるか |
| 主操作 | どの操作が次へ進む主要操作か一目で分かるか |
| 作業記憶 | スクロールや画面移動中に覚え続ける必要がある情報は何か |
| 再入力 | 前段で確定した情報を再選択・再入力していないか |
| 往復 | 条件と結果を確認するために何回、どこを往復するか |
| 状態識別 | loading、stale、partial、failed、savedを区別できるか |
| 復旧 | errorから次の有効な操作まで何手必要か |
| 不可逆性 | 固定・削除・公開等の戻せない操作はどこで理解できるか |
| 詳細依存 | 内部parameterを理解しないと安全に進めないか |
| 結果位置 | 操作後の結果が予測した場所に現れるか |
| 証拠対応 | 数値と、その条件・identity・不確かさを結び付けられるか |

特に、利用者のworking memoryへ預けている情報を具体的に書きます。

悪い例:

> 上部で選んだObjectiveを覚えたまま下へスクロールし、候補表を読み、再び上へ戻ってparameterを変更する。

改善の方向:

> 現在のObjectiveを結果の近くで要約し、変更はcontextを保ったside panelから行う。設定完了位置にも実行操作を置く。

### Step 4: 異なる認知構造の案を比較する

構造変更を伴う場合、原則として少なくとも二案を比較します。

別案として数えるもの:

- 一画面＋progressive disclosure
- 目的ごとのmode分割
- 段階的step flow
- 結果中心＋設定side panel
- 一覧からdetailへdrill down
- graph＋同等のlinear list

別案として数えないもの:

- cardの角丸・色・余白だけが違う
- 同じ項目をaccordionへ入れただけ
- 左右を入れ替えただけで、判断順序が同じ

比較する観点:

- 主操作までの手数
- 同時に考える概念
- 覚え続ける情報
- 誤操作と取り違え
- 再編集とresume
- deep linkとback／forward
- small viewport
- keyboard／screen reader
- server stateとdraft state
- immutable evidenceへの接続
- 実装・migration・testの複雑さ

実装コストは判断材料ですが、利用者の認知作業を悪化させる唯一の理由にはしません。

### Step 5: 主要要素の配置理由を書く

主要な情報・controlについて、「なぜここか」を説明します。

| 要素 | 配置理由の例 |
|---|---|
| 主実行ボタン | 設定を完了した位置から戻らず実行できるため |
| 前提不足の警告 | 影響を受ける操作・数値より先に読む必要があるため |
| 現在のObjective要約 | 提案結果を解釈するとき常に参照するため |
| seed／digest | 実行前の判断には不要だが監査には必要なため技術詳細へ置く |
| Estimator選択 | 列の意味とTask契約を確定した後に選ぶべきため |
| errorと再試行 | 失敗したresourceの場所で原因と次の操作を対応付けるため |
| fixed binding review | 前段で確定したidentityを再選択させないため |

次の説明は不十分です。

- 関連情報だから
- 既存画面にあるから
- 一般的なdashboardだから
- componentを再利用できるから
- spaceが空いているから

### Step 6: progressive disclosureを設計する

情報を隠すか見せるかの二択ではなく、読む順序を設計します。

既定表示に置く候補:

- 現在の問い
- 必須の前提
- 主要な選択
- 直近の結果と状態
- 判断を変えるwarning
- 主操作

必要時に開く候補:

- seed、digest、request ID
- strategy／runtime内部ID
- 全provenance payload
- advanced parameter
- raw JSON
- migration／compatibility detail

利用不能な機能は、黙って消す、空panelで残す、押した後に失敗させる、のいずれにも固定しません。利用者の問いに必要ならdisabled＋理由＋代替、不要なら主画面から外してcapability詳細で確認できるようにします。

### Step 7: 受入観察を先に書く

実装完了をDOM shapeやcomponent名だけで定義しません。利用者が実画面で達成できる観察を書きます。

例:

- 初回利用者がstrategy名を知らずに「有望候補を探す」を選び、実行できる
- 詳細parameterを開かず、安全な既定値で進める
- 設定完了位置から主実行ボタンが見える
- 結果と現在のObjectiveを画面往復なしで確認できる
- 利用不能なstrategyは実行前に理由と近い代替を確認できる
- warningは影響対象の数値より前に読める
- onboardingで確定したDataset／Task／Packageを再選択せずProjectを作れる
- errorが起きたresourceだけを再試行し、取得済みの別resourceは残る
- browser back／forwardで直前の選択文脈へ戻れる

### Step 8: 実装する

実装では次を守ります。

- 画面構造の問題をcopyやCSSだけへ縮小しない
- backend／contract変更が必要ならUIだけで擬似解決しない
- generated OpenAPI typeをcastで迂回しない
- server state、draft、URL state、presentation metadataを混ぜない
- stale response rejection、revision、digest、snapshotの意味を弱めない
- Task ID、モデル名、元データ列名による中央分岐を増やさない
- arbitrary pluginや万能schemaを認知負荷改善の名目で導入しない
- 旧経路を並存させず、必要なmigrationを行って完全移行する

### Step 9: 実画面で反証する

実装者の期待どおりに動く確認だけでは不十分です。fresh server／独立Workspaceで、少なくとも次を確認します。

- 初回状態
- 入力途中
- loading
- partial result／stale
- field error
- resourceの部分失敗
- unavailable capability
- back／forwardまたは再読み込み
- small viewportまたは文字拡大
- keyboard操作
- 保存後のresume

構造変更が大きい場合は、実装コードや期待findingを先読みしないActorで短いUI-first journeyを行います。長い意思決定journeyが必要な場合はScenario Journey Evaluatorへ引き渡します。

## UX Change Briefテンプレート

Issueを必須とはしません。PR本文、設計メモ、作業記録のいずれかへ、変更規模に応じた簡潔さで残します。

```markdown
## 利用者の問い

誰が、何を知りたい／達成したいか。

## 到達時点で確定していること

Project、Dataset、Task、Package、Candidate、Objective等、既に確定した文脈。

## この画面で決めること

利用者が新たに判断・入力すること。

## この画面で決めさせないこと

関連はあるが現在の問いには不要なこと。

## 現在のjourney

入口 → 操作 → 結果 → 復旧／次の作業。

## 認知負荷

- 初見概念:
- 同時選択:
- 主操作:
- 覚え続ける情報:
- 再入力:
- 往復:
- 状態／error:
- 不可逆操作:

## 構造案

### 案A

認知モデル、利点、欠点。

### 案B

認知モデル、利点、欠点。

## 採用案と配置根拠

主要要素ごとに、なぜその順序・位置か。

## 既定表示と技術詳細

何を最初に見せ、何を必要時に開くか。

## 削除・統合・後送り

追加するものだけでなく、同時表示から外すもの。

## 守る証拠とidentity

prediction／actual、uncertainty、support、revision、digest、Run、Snapshot等。

## 受入観察

実画面で利用者が何を達成・理解できれば完了か。

## 反証結果

fresh UIで確認した事実、残る摩擦、未確認事項。
```

## レビュー質問

最低限、次をレビューします。

1. このviewportで利用者が答える問いは明確か。
2. 主操作は一つに見えるか。
3. 利用者が覚え続ける必要のある情報は何か。
4. 前段で確定した情報を再入力・再選択させていないか。
5. 結果と、その結果を作った条件・identityを往復なしで確認できるか。
6. 詳細設定を理解しなくても安全な既定経路を進めるか。
7. 利用不能、前提不足、stale、partial resultを実行前または数値より先に理解できるか。
8. 各主要要素の配置理由を「関連しているから」以外で説明できるか。
9. copyやtooltipで構造的問題を隠していないか。
10. 科学的な証拠を認知負荷削減の名目で欠落させていないか。
11. keyboard、screen reader、small viewportでも同じ問いに答えられるか。
12. testがDOM実装ではなく利用者の受入観察を検証しているか。

## よくある失敗

### 整理したが減らしていない

20項目を整列された20項目に変え、同時判断数を減らしていない。

### 説明文で導線を補う

操作の順序や結果位置を変えず、「まず上を設定してください」と説明する。

### 全設定を同じ階層に置く

安全な既定値、業務上の選択、再現性parameterを同時に見せる。

### 主操作が作業開始地点にしかない

長い設定を終えた後、画面上部へ戻らないと実行できない。

### warningが数値の後ろにある

support外100%等の重大な前提を、精密な予測値の後で小さく表示する。

### 前画面の記憶を要求する

前段で選んだDatasetやObjectiveを覚え、別画面で再選択させる。

### errorをtoastだけへ出す

何が失敗し、どの情報が残り、何を再試行するかをresourceの場所で示さない。

### 内部概念を翻訳しただけ

Runtime、strategy、digest等を日本語化しても、利用者の問いへ変換していない。

### accessibility passを使いやすさの証明にする

読み上げ可能でも、情報の順序、比較、復旧が理解できない。

### 既存component境界を優先する

利用者の作業順序より、既存state ownerや再利用しやすいcomponentを優先する。

### 「慣れれば分かる」を前提にする

初回利用者の混乱をtrainingやdocumentationへ押し戻す。

## 完了条件

UI構造または操作経路を変えた作業は、次を満たしたとき完了です。

- 利用者の問い、確定済み文脈、今回決めること、決めさせないことが記録されている
- 現在の認知負荷とworking memory要求を確認した
- 追加前に削除・統合・後送りを検討した
- 構造変更では認知モデルの異なる案を比較した、または単一案で十分な理由がある
- 主要要素の順序と配置に理由がある
- scientific evidence、identity、uncertainty、supportを保っている
- 利用者の受入観察が定義されている
- fresh UIで正常、途中、失敗、復旧、resumeを必要範囲で反証した
- typecheck、unit、E2E、axe、screenshotを使いやすさの唯一の証明にしていない
- 残る摩擦と未確認事項を隠していない
