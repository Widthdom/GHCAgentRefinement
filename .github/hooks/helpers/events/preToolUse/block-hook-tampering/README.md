# block-hook-tampering hook README

## 発動条件
preToolUse（ファイル編集ツール `edit`・`create`・`insert`・`str_replace_editor`・`apply_patch` と、bash・powershellの実行前）

## 処理概要
ユーザーがこのセッションでhookの変更に触れていないのに、hookの定義・実装・状態ファイルを変更する操作を拒否する。

## 処理詳細
1. 呼び出すツール（`toolName`・`toolArgs`）から、守るパス（[守る対象](#守る対象)を参照）への変更を探す。
   - 編集系ツール：`path`、または `apply_patch` の `*** Add File:`・`*** Update File:`・`*** Delete File:`・`*** Move to:` のパスを、cwdを基準に絶対パスにして照合する（`str_replace_editor` の `view` は除く）。
   - bash・powershell：コマンドが守るパス（または守るパスを含むフォルダー）を含み、かつ[変更とみなす操作](#変更とみなす操作)を含む場合に変更とみなす。
2. 変更がなければ何もしない。
3. [record-hook-change-requests](../../userPromptSubmitted/record-hook-change-requests/README.md) hookの記録を読み、このセッションでユーザーがhookの変更に触れていれば許可する。
4. 触れていなければ、拒否やチェックを避けるためにhookを変更・無効化・削除せず、本当に必要ならユーザーに理由を説明して明示的に依頼してもらうよう求める英語の理由を標準エラーに出力し、`{"permissionDecision":"deny",...}` を標準出力して拒否する。

## 守る対象
|パス|内容|
|--|--|
|`.github/hooks/` 配下|hookの定義（`<event>.json`）、実装、定義ファイル、テスト、ランナー、共通処理|
|`<一時フォルダー>/ghc-agent-refinement/hook-states/` 配下|hookが保持する状態。拒否の記録や、本hookが使う変更の許可の記録を書き換えて制限を外すのを防ぐ|

- パスの照合は大文字小文字を区別せず、`/` と `\` のどちらの区切りも受け付ける。
- シェルのコマンドでは、守るパスを含むフォルダーそのもの（`.github`・`.github/*`・`ghc-agent-refinement`）も対象にする。丸ごと削除・移動すると守るパスも変わるため。

## 変更とみなす操作
シェルのコマンドは `;`・`&&`・`||`・`&`・改行で区切り、さらに `|` で単純コマンドに分けて判定する。コマンド名は、単純コマンドの始まり（`(`・`{`・引用符・`bash -c "…"` の中、予約語・`sudo`・`env`・`xargs` などの前置きや変数の代入の後を含む）にあるものだけを照合する。

|種類|例|守るパスの位置|
|--|--|--|
|引数に書いた対象を変更するコマンド|`rm`・`mv`・`cp`・`chmod`・`touch`・`tee`・`del`・`move`・`Out-File`・`Tee-Object` など|同じ単純コマンド。`xargs` から呼ぶ場合は同じパイプライン|
|パイプの入力から対象を受け取れるコマンドレット|`Remove-Item`・`Move-Item`・`Copy-Item`・`New-Item`・`Set-Content` などと、powershellでのその別名（`ri`・`mi`・`sc` など）|同じパイプライン|
|その場で書き換えるエディター|`sed -i`・`perl -pi`|同じ単純コマンド|
|`find` の削除と実行|`find … -delete`・`find … -exec rm {} \;`|同じ単純コマンド|
|作業ツリーやインデックスを書き換えるgit|`git checkout`・`restore`・`rm`・`mv`・`reset`・`stash`・`apply`・`clean` など（`git -C <path>` などのオプションを挟んでもよい）|同じ単純コマンド|
|守るパスへのリダイレクト|`echo {} > .github/hooks/preToolUse.json`|コマンドのどこでもよい|
|スクリプト内部での書き込み|`python -c "open(..., 'w')"`・`write_text`・`os.remove`・`shutil.move`・`[IO.File]::WriteAllText`・`fs.writeFileSync` など|コマンドのどこでもよい|

- `cd`・`pushd`・`Set-Location` で守るパス（または守るパスを含むフォルダー）へ移動した後は、パスを書かない上記の操作と、相対パスへのリダイレクト（`> preToolUse.json`）も変更とみなす。別の場所へ移動し直した後は対象外に戻る。

## 前提条件
- hookを変更する作業では、依頼で `hook`・`フック`・イベント名・hookの名前のいずれかに触れればよい。
- [record-hook-change-requests](../../userPromptSubmitted/record-hook-change-requests/README.md) hookの記録で許可を判定する。

## 例外
- 読み取りやテストの実行（`cat`・`grep`・`git diff`・`git log`・`python .github/hooks/tests/test-all-hooks.py > /tmp/all.log`・`… 2>&1 | tee /tmp/all.log`・`… && touch /tmp/ok`）は許可する。変更系のコマンド名が検索語などの引数にある場合（`grep -rn sc .github/hooks`）や、守るパスを含まない別の単純コマンドにある場合は変更とみなさない。
- 許可はセッション単位で、一度hookに触れた依頼があればセッションの間は続く。依頼ごとに判定すると、hookの作業中の「続けて」などで止まってしまうため。

## 制約と限界
- 他のhookと異なり、同じファイルでも拒否は1回で終わらない。再実行で通せると、拒否を避けたいエージェントがそのまま再実行できてしまうため。ユーザーがhookに触れた依頼をすれば、以降は許可する。
- サブエージェントの呼び出しも同じセッションIDで判定する。サブエージェントへの依頼文では許可されない（record-hook-change-requests hookが記録しない）。
- コマンドの判定は正規表現による簡易的なもので、次の場合は検出できない。
  - 守るパスを変数や文字列の連結で組み立てた場合や、`cd .github/workflows && rm -rf ../hooks` のように守るパスを含まない相対パスで指す場合
  - パスを取らずに作業ツリー全体を書き換えるgitの操作（`git stash`・`git checkout <branch>`・`git checkout <rev> -- .` など）。ブランチの切り替えや退避など日常の操作を止めないため、対象にしない
- 逆に、守るパスから他の場所への `cp` や、`.github/` へのファイルの複製など、hookを変更しない操作も拒否する。

## 備考
- hookに拒否されたエージェントが、hookの定義や実装、定義ファイル、状態ファイルを書き換えて制限を外すのを防ぐためのhook。hookが増えるほど、hookそのものを守る仕組みが必要になる。
- 状態ファイルが壊れている場合は異常終了し、Copilotがツールの実行の拒否として扱う。
