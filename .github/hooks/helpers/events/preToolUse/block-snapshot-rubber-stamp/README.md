# block-snapshot-rubber-stamp hook README

## 発動条件
preToolUse（bashまたはpowershellツールの実行前）

## 処理概要
スナップショットやゴールデンファイルを現在の出力で一括承認するコマンドを拒否し、差分を確かめてから実装を直すか、意図した変更として報告するよう求める。

## 処理詳細
1. [snapshot-update-definitions.json](./snapshot-update-definitions.json) から、一括更新の種類ごとの名前と正規表現（`updateCommands`）を読み込む。ファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. 呼び出すツール（`toolName`・`toolArgs`）がbash・powershellなら、その `command` を `;`・`&&`・`||`・`|`・改行で区切り、区切った単位ごとに `updateCommands` の正規表現と照合する。
3. 一致した種類のうち、このセッションでまだ拒否していないものがあれば、テストを更新なしで実行して差分を確かめるよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 検出するコマンド

|種類|例|
|--|--|
|Jest・Vitest・Playwrightの `-u`・`--update`|`npx jest -u`・`npm test -- -u`・`npx vitest run -u`|
|スナップショット更新のオプション|`--update-snapshots`・`--updateSnapshot`・`--snapshot-update`（syrupy・snapshottest）|
|pytest-regressionsの再生成|`pytest --force-regen`|
|inline-snapshotの書き換え|`pytest --inline-snapshot=fix,create`|
|instaの承認|`cargo insta accept`・`cargo insta review`・`cargo insta test --accept`・`INSTA_UPDATE=always`|
|Verifyの承認|`dotnet verify accept`|
|Goのゴールデンファイル更新|`go test ./... -update`・`-update-golden=true`|
|更新用の環境変数|`UPDATE_SNAPSHOTS=1`・`UPDATE_GOLDEN=true`・`$env:UPDATE_SNAPSHOTS = '1'` など|

## 例外
- `-u`・`--update` は、同じ区切りの単位にテストランナー（`jest`・`vitest`・`playwright`・`npm test` など）がある場合だけ対象とする。`git push -u`・`python3 -u`・`sort -u` は対象外。
- 更新なしのテスト実行（`npx jest`・`cargo insta test`）や、値を無効にする代入（`UPDATE_SNAPSHOTS=0`）は対象外。

## 制約と限界
- 拒否は同じ種類につきセッションで1回だけ。ユーザーが更新を依頼した場合など意図した更新は、同じコマンドを再実行すれば通る。
- 照合はコマンドの文字列に対する正規表現で、引用符の中やコメントも区別しない。`git commit -m "npx jest -u"` などでも拒否しうる。
- コードで承認する設定（Verifyの `AutoVerify()` など）や、スナップショットファイルの直接編集は対象外。

## 背景
テストの失敗を、出力を承認して「直す」のは、期待値を実際の値に書き換える [block-goalpost-moving](../block-goalpost-moving/README.md) hookの対象と同じ骨抜きを、コマンド1つで全テストにまとめて行うことになる。編集ではなくコマンドで行われるため、編集を見るhookでは防げない抜け道を塞ぐ。

## 備考
- 状態には種類の名前だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/block-snapshot-rubber-stamp/<セッションID>.sqlite3` に保存する。拒否しないコマンドでは状態ファイルを作らない。
- 正規表現はPythonの `re` 構文で書き、大文字小文字を区別しない場合は `(?i)` を先頭に付ける。テスト用に `--definitions-path` で定義ファイルを差し替えられる。
- 不正な入力では異常終了する。Copilotはhookの異常終了をツール実行の拒否として扱う。
