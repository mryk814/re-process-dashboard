# Windows配布

## 現在の配布形態

同じ `win-unpacked` ステージングから、次の2つを生成する。

- `Evidence-Decision-Workbench-Setup-<version>.exe`: 管理者権限を要求しないper-user installer
- `Evidence-Decision-Workbench-folder-<version>.zip`: 内部確認・開発者共有向けの展開式フォルダ版

どちらもPython、uv、repository checkoutを必要としない。FastAPI sidecar、使用するExcel、検証済みModel Packageを同梱する。コード署名、GitHub Releases、自動更新は現段階では行わず、信頼できる社内経路から手動配布する。

## 生成と確認

```powershell
npm install
uv sync --extra dev
npm run package:windows
```

成果物はgit管理外の `release/` に作る。`package:windows` は必要resourceの同梱を検査した後、ZIPの展開・起動・削除と、installerの非管理者install・起動・uninstallを一時領域で実行し、次を確認する。不完全な成果物やsmoke失敗時はコマンド自体が失敗する。

## 現mainの統合受入

Actionsを使わず、backend、Web／Desktop、clean DBの全E2E、旧DB移行、Windows配布物までを一続きで再確認するときは、コミット済みのcleanなworktreeで次を実行する。

```powershell
npm run acceptance:release -- -ReportPath docs/reports/main-acceptance-YYYY-MM-DD.json
```

このコマンドは各gateのlogを`artifacts/main-acceptance/<run-id>/`へ保存し、対象commit、環境、成功件数、所要時間、installer／folder ZIPのサイズとSHA-256を指定したJSONへ記録する。Playwrightは常駐サーバを再利用せず、実行ごとの一時DBを使う。途中で失敗しても、完了済みgateと失敗箇所を含む部分reportを残す。

- sidecar health後に実画面が表示される
- tokenなしのloopback API requestが401になる
- rendererが付与するtokenではAPIへ到達できる
- repository外の同梱Excel・Model Packageで起動できる
- フォルダ内にDBとsidecar logが作られる
- installer版のDBとlogがLocalAppDataへ作られ、uninstall後も利用データが保持される
- native file dialogからWorkspace backupを作成し、改ざんbundleを拒否した後、
  正常bundleを復元してAPIと実画面を再起動できる

## 保存先と削除

installer版は、既存Workspaceとの互換のため、旧製品名を含む
`%LOCALAPPDATA%\Material Decision Workbench` にDBとログを保存する。
アンインストール時に利用データを自動削除しない。
完全に消す場合は、アンインストール後にこのフォルダを利用者が明示的に削除する。

フォルダ版は展開先の `user-data/` にDBとログを保存する。削除するときはアプリを終了し、展開したフォルダを丸ごと削除できる。`portable.marker` が保存先切替の印であり、削除・移動しない。

元ExcelとModel Packageはどちらの形式でも読取専用の配布resourceとして扱う。DBやログをインストール先の `resources/` へ書かない。
利用者データの移行にはDBの手動コピーではなく
[Workspaceバックアップ／復元](workspace-backup.md)を使う。

## 配布時の注意

- 未署名のためWindows SmartScreenの警告が出る可能性がある。配布時にファイル名、version、SHA-256を併記する。
- installerとZIPは必ず同じ `npm run package:windows` 実行で生成する。
- ZIPへ `user-data/` が混入していないことを確認する。
- 更新機構、Model Package/DataのGUI import、署名は後続段階とする。
