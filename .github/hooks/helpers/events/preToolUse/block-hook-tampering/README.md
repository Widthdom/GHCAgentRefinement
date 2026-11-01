# block-hook-tampering hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` と、bash・powershellの実行前）

## 処理詳細
1. 呼び出すツール（`toolName`・`toolArgs`）から、守るパス（[守る対象](#守る対象)を参照）への変更を探す。
   - 編集系ツール：`path`、または `apply_patch` の `*** Add File:`・`*** Update File:`・`*** Delete File:`・`*** Move to:` のパスを、cwdを基準に絶対パスにして照合する（`str_replace_editor` の `view` は除く）。
   - bash・powershell：コマンドが守るパスを含み、かつ[変更とみなす操作](#変更とみなす操作)を含む場合に変更とみなす。
2. 変更がなければ何もしない。
3. [record-hook-change-requests](../../userPromptSubmitted/record-hook-change-requests/README.md) hookの記録を読み、このセッションでユーザーがhookの変更に触れていれば許可する。
4. 触れていなければ、拒否やチェックを避けるためにhookを変更・無効化・削除せず、本当に必要ならユーザーに理由を説明して明示的に依頼してもらうよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 守る対象
|パス|内容|
|--|--|
|`.github/hooks/` 配下|hookの定義（`<event>.json`）、実装、定義ファイル、テスト、ランナー、共通処理|
|`<一時フォルダー>/ghc-agent-refinement/hook-states/` 配下|hookが保持する状態。拒否の記録や、本hookが使う変更の許可の記録を書き換えて制限を外すのを防ぐ|

- パスの照合は大文字小文字を区別せず、`/` と `\` のどちらの区切りも受け付ける。

## 変更とみなす操作
|種類|例|
|--|--|
|削除・移動・複製・作成などのコマンド|`rm`・`mv`・`cp`・`chmod`・`touch`・`tee`・`del`・`move`・`Remove-Item`・`Move-Item`・`Copy-Item`・`New-Item`・`Set-Content`・`Out-File` と、PowerShellの別名（`ri`・`mi`・`sc` など）|
|その場で書き換えるエディター|`sed -i`・`perl -pi`|
|作業ツリーやインデックスを書き換えるgit|`git checkout`・`restore`・`rm`・`mv`・`reset`・`stash`・`apply`・`clean` など（`git -C <path>` などのオプションを挟んでもよい）|
|守るパスへのリダイレクト|`echo {} > .github/hooks/preToolUse.json`|
|スクリプト内部での書き込み|`python -c "open(..., 'w')"`・`write_text`・`os.remove`・`shutil.move`・`[IO.File]::WriteAllText`・`fs.writeFileSync` など|

- 読み取りやテストの実行（`cat`・`grep`・`git diff`・`git log`・`python .github/hooks/tests/test-all-hooks.py > /tmp/all.log`）は許可する。

## 備考
- 特別なプロンプト指定は不要。hookを変更する作業では、依頼で `hook`・`フック`・イベント名・hookの名前のいずれかに触れればよい。
- hookに拒否されたエージェントが、hookの定義や実装、定義ファイル、状態ファイルを書き換えて制限を外すのを防ぐためのhook。hookが増えるほど、hookそのものを守る仕組みが必要になる。
- 他のhookと異なり、同じファイルでも拒否は1回で終わらない。再実行で通せると、拒否を避けたいエージェントがそのまま再実行できてしまうため。ユーザーがhookに触れた依頼をすれば、以降は許可する。
- 許可はセッション単位で、一度hookに触れた依頼があればセッションの間は続く。依頼ごとに判定すると、hookの作業中の「続けて」などで止まってしまうため。
- サブエージェントの呼び出しも同じセッションIDで判定する。サブエージェントへの依頼文では許可されない（record-hook-change-requests hookが記録しない）。
- コマンドの判定は正規表現による簡易的なもので、守るパスを変数や文字列の連結で組み立てた場合は検出できない。逆に、守るパスから他の場所への `cp` など、hookを変更しない操作も拒否する。
- 状態ファイルが壊れている場合は異常終了し、Copilotがツールの実行の拒否として扱う。
