# hookレビュー結果

- 対象：`main`（908bdf9）時点の `.github/hooks/` 配下（定義JSON 8本、hook 63本、ランナー、utility、テスト）と、関連するskill・README・CIワークフロー
- 実施日：2026-11-11
- コードの修正は行っていない。本ファイルの追加だけ。

## 結論

- テストはPython 3.14.6ですべて成功し、不正入力のfuzzでも未捕捉の例外は出なかった。定義JSON・フォルダー・READMEの対応にもずれはない。
- 一方で、**拒否hookをすり抜ける経路**が複数ある。特に `block-commands` は、`toolArgs` がJSON文字列で渡されると拒否が一切効かない（No.1）。Copilotが実際にどちらの形で渡すかは、この環境から公式ドキュメントに到達できず未確認のため、最優先で確認したい。
- 次点は、共有utilityの `split-bash-lines` の字句解析の誤り（No.2・3）。これを使う `block-shell-file-edits`・`block-masked-exit-codes` の検査が、後続の行ごと外れる。

## 実施内容

|確認|方法|結果|
|--|--|--|
|一括テスト|`test-all-hooks.py`（Python 3.14.6、Linux）|93ファイルすべて成功|
|定義の検証|`test-hook-configurations.py`|成功|
|テスト出力の検証|`test-hook-test-output.py`|成功|
|Python 3.13での一括テスト|同上（3.13.18）|`test-block-commands.py` の1件だけ失敗。`.sh` が3.14未満を拒否する仕様どおりで、問題ではない|
|不正入力のfuzz|全hookに約90種の入力（空・非JSON・項目の欠落・型違い・JSON文字列の `toolArgs`・壊れたトランスクリプトなど）を直接渡した|未捕捉の例外・終了コード0/2以外はなし。1回の実行は最長0.23秒|
|READMEとの対応|`.github/README.md`・`.github/-repo-README.md` の表と定義JSONを照合|63本すべて一致。hookごとのREADME・テストの欠落なし|
|コード読解|ランナー、utility、sessionStart・userPromptSubmitted・postToolUseFailureの全hook、状態を持つhook、守りの要になるpreToolUse hook|下記の指摘|
|挙動の確認|指摘ごとに実際の入力でhookを起動|「確認」列に記載|

### 確認できていないこと

- Windows・macOSでの動作（`.cmd`、PowerShellの字句解析、パスの大文字小文字）。CIのマトリックスに任せている。
- Copilot本体との結合（`matcher` の照合方法、hookの並列実行、タイムアウト時の扱い、`hook.start` の `timestamp` の形式、postToolUseFailureの `error` の型）。公式の入出力仕様（docs.github.com）はこのセッションのネットワーク設定で到達できなかった。
- 検出系hook（`block-test-weakening`・`detect-orphaned-additions` など約40本）の正規表現ごとの誤検知・検出漏れ。fuzzとテストの成功までで、1本ずつの精査はしていない。

## 指摘一覧

重大度は、高＝守りが効かない／中＝限定的な条件ですり抜け・誤拒否・停止／低＝改善の余地、とした。

|No.|重大度|対象|内容|確認|
|--|--|--|--|--|
|1|高|block-commands|`toolArgs` がJSON文字列だと拒否しない|再現|
|2|高|split-bash-lines|引用符や算術式の中の `<<` をヒアドキュメントと誤認し、後続の行を検査から外す|再現|
|3|中|split-bash-lines|語の途中の `#` をコメントとして扱い、行の残りを捨てる|再現|
|4|中|block-commands|先頭の空白、`(`、`then`、`env` などの後の `git` を拒否しない。`-uf`・`+refspec` の強制pushも通る|再現|
|5|中|block-hook-tampering|守るパスの親を操作するコマンドを拒否しない|再現|
|6|中|record-hook-change-requests|`webhook`・`フック` を含むだけの依頼でhookの変更を許可する|再現|
|7|中|block-hook-tampering|READMEが許可とする `grep` やテスト実行を、語によっては拒否する|再現|
|8|中|require-hook-tests・verify-test-pass-claims|テストを実行していないコマンドや、終了コードを隠したコマンドを成功とみなす|再現|
|9|中|debug-add-response-footer|差し戻しの回数に上限がなく、サブエージェントの停止も対象にする|再現|
|10|低|wait-for-agent-stop-event|照合できない場合、agentStop hook 13本がそれぞれ5秒待つ|再現（1本）|
|11|低|block-commands|コミットメッセージなど引用符の中の `; git reset` を拒否する|再現|
|12|低|simulate-edit-tool-changes|`old_string`・`new_string` を読まず、extract-edit-tool-changesと対応がずれる|読解|
|13|低|set-debug-mode・set-adversarial-review-state・hook-change-request-state|状態ファイルを直接上書きする（一時ファイル経由でない）|読解|
|14|低|run-hook|UUIDでない `sessionId` で、全hookが記録の失敗を標準エラーに出す|読解|
|15|低|clean-hook-temp-files|スクリプト本体（`--dry-run` の出力など）のテストがない|読解|

## 詳細

### 1. block-commands：JSON文字列の `toolArgs` で拒否しない（高）

[block-commands.py](./.github/hooks/helpers/events/preToolUse/block-commands/block-commands.py) は、`toolArgs` が文字列ならそのままコマンドとして扱う。定義の正規表現は `git` の前に行頭か `;&|` を求めるため、JSONに包まれた形では一致しない。

```
{"toolName":"bash","toolArgs":{"command":"git reset --hard"}}            → deny
{"toolName":"bash","toolArgs":"{\"command\":\"git reset --hard\"}"}    → {}（許可）
```

他のhookはすべて [unwrap-tool-args.py](./.github/hooks/helpers/utilities/unwrap-tool-args/unwrap-tool-args.py) を通しており、そのコメントも「JSON文字列に包まれて渡される場合もある」としている。block-commandsだけが外していない。テストも文字列の `toolArgs` を「生のコマンド」（`"toolArgs":"git reset"`）としてしか検証していない。

- 対応案：`unwrap_tool_args` を通してから `command` を取り出す。JSONとして解析できない文字列だけを生のコマンドとして扱う。JSON文字列で包んだ拒否のテストを加える。
- 補足：Copilotが実際に渡す形は未確認。オブジェクトでしか渡されないなら実害はないが、他のhookと前提を揃える意味でも直したい。

### 2. split-bash-lines：`<<` の誤認で後続の行が検査から外れる（高）

[split-bash-lines.py](./.github/hooks/helpers/utilities/split-bash-lines/split-bash-lines.py) は、字句に分けた後の行の文字列全体から正規表現でヒアドキュメントの開始を探す。引用符の中や算術式の `<<` も開始とみなし、区切りの語と同じ行が現れるまで、後続の行をすべて本文として読み飛ばす。

```
echo "a << b"            ← `b` を区切りの語とするヒアドキュメントと誤認
echo x > CLAUDE.md       ← 読み飛ばされ、block-shell-file-editsが許可

x=$((1 << 2))
echo x > CLAUDE.md       ← 同上
```

同じutilityを使う `block-masked-exit-codes` でも、後続の行の `npm test | tail` などが検査されない。

- 対応案：ヒアドキュメントの開始は、字句のリストに `<<`・`<<-` の演算子が現れたかで判定する（引用符の中は1つの語になるため誤認しない）。`$((` … `))` の中は対象外にする。

### 3. split-bash-lines：語の途中の `#` をコメントとして扱う（中）

`shlex` は語の途中の `#` もコメントの開始とする。bashでは語の先頭の `#` だけがコメントになる。

```
sed -i s#a#b# CLAUDE.md   → ['sed', '-i', 's']        対象のファイルが落ち、許可
echo a#b > CLAUDE.md      → ['echo', 'a']             リダイレクトが落ち、許可
```

- 対応案：`lexer.commenters = ''` にして、語の先頭が `#` の字句以降を自前で捨てる。[block-commit-message-mismatch.py](./.github/hooks/helpers/events/preToolUse/block-commit-message-mismatch/block-commit-message-mismatch.py) は既に `commenters = ''` にしている。

### 4. block-commands：`git` の前置きによるすり抜け（中）

定義の正規表現が `(?:^|[;&|]\s*)git` で始まるため、次のコマンドはどれも許可された。READMEに「制約と限界」の章はなく、限界として書かれてもいない。

```
  git reset --hard                     先頭の空白
(git reset --hard)                     サブシェル
if true; then git reset --hard; fi     予約語の後
env git reset --hard                   前置きのコマンド（正規表現上、sudo・command・time も同じ）
git push -uf origin main               まとめた短いオプション
git push origin +main                  refspecでの強制
```

- 対応案：`^` の後に `\s*` を許し、`(`・`{`・`` ` ``・`$(`・`then`・`do`・`else` と前置きのコマンドを区切りに加える。完全には塞げないため、残る限界はREADMEの「制約と限界」に書く。

### 5. block-hook-tampering：守るパスの親への操作を拒否しない（中）

コマンドの文字列に `.github/hooks` が現れることを条件にしているため、親や作業ツリー全体への操作は通る。

```
cd .github && rm -rf hooks      rm -rf .github      mv .github .github2
git checkout HEAD~5 -- .        git stash           find .github -name "*.json" -delete
```

READMEの限界は「変数や文字列の連結で組み立てた場合」だけで、これらには触れていない。

- 対応案：`.github` 自体と、パスを取らずに作業ツリー全体を書き換えるgitの操作（`stash`・`checkout -- .`・`reset --hard`・`clean`）を条件に加える。少なくともREADMEの限界に書く。

### 6. record-hook-change-requests：hookと無関係な依頼でも許可する（中）

`_HOOK_REQUEST_PATTERN` の `hooks?`・`フック` に語の境界がなく、次の依頼の後は `.github/hooks/preToolUse.json` の編集が許可された。許可はセッションの最後まで続く。

```
Stripeのwebhookを実装して / ReactのカスタムフックuseFooを追加 / fix the fishhook bug
```

- 対応案：`(?<![A-Za-z])hooks?(?![A-Za-z])` のように英字の途中を除く。`フック` は `カスタムフック`・`Reactフック` などを除くか、hook名・イベント名・`.github/hooks` との組み合わせを求める。

### 7. block-hook-tampering：読み取り・テスト実行の誤拒否（中）

変更のコマンドの一覧にPowerShellの別名（`sc`・`ac`・`ni`・`ri`・`mi`）や `tee`・`touch`・`cp` があり、bashのコマンドの引数や後続のコマンドにも一致する。READMEが許可とする操作でも拒否された。

```
grep -rn sc .github/hooks                                          検索語が `sc`
python3 .github/hooks/tests/test-all-hooks.py 2>&1 | tee /tmp/log  出力を一時ファイルへ
python3 .github/hooks/tests/test-all-hooks.py && touch /tmp/ok
```

ユーザーがhookに触れていないセッションに限るが、拒否は毎回行われるため、エージェントはテストの出力を保存できない。

- 対応案：PowerShellの別名はツールがpowershellのときだけ照合する。`tee`・`cp`・`touch` などは、書き込み先の引数が守るパスのときだけ拒否する。

### 8. require-hook-tests・verify-test-pass-claims：実行していないテストを成功とみなす（中）

- [require-hook-tests.py](./.github/hooks/helpers/events/agentStop/require-hook-tests/require-hook-tests.py) は、コマンドにテストファイル名が含まれ、終了コードが0なら実行とみなす。`cat …/test-block-commands.py`・`git add …/test-block-commands.py` でも要求を満たした。
- どちらのhookも、コマンド全体の終了コードで成否を決める。`python3 test-x.py; echo done`・`python3 test-x.py || true` は、テストが失敗しても成功とみなす。
- verify-test-pass-claimsは `ls tests`・`git log --oneline | grep test` もテストの実行とみなした。`block-masked-exit-codes` にある「読むだけのコマンド」の除外が、こちらにはない。

- 対応案：読むだけのコマンドの除外を [check-test-runs.py](./.github/hooks/helpers/utilities/check-test-runs/check-test-runs.py) に移して3つのhookで共有する。テストの後に `;`・`||` で別のコマンドが続く場合は、終了コードを根拠にしない。

### 9. debug-add-response-footer：差し戻しの上限とサブエージェント（中）

- 他のagentStop hookは `is_already_requested` で同じ依頼での差し戻しを1回に限るが、本hookにはない。差し戻しが3回続いたトランスクリプトでも、4回目を差し戻した。フッターの内容（機能したhook・時刻）は差し戻しのたびに変わるため、エージェントが合わせ損ねると止まらない。
- `agentId` を見ていないため、サブエージェントの停止でもフッターを求める。他の14本はすべて `agentId` 付きの入力を対象外にしている。

デバッグモード（`??` で始まる依頼）のときだけの問題。

- 対応案：`agentId` 付きの入力を対象外にする。差し戻しの回数を数え、上限（2〜3回）を超えたら通す。

### 10. wait-for-agent-stop-event：照合できない場合の待ち時間（低）

`hook.start` の `input.timestamp` がhook入力の `timestamp` と一致しないと、呼び出したhookは毎回5秒待つ（1本で5.1秒を確認）。待つhookは13本ある。[共通規約](./.github/skills/create-hook/references/hook-conventions.md)の項目1は「`hook.start` の `input` は標準入力と形式が異なる場合がある」としており、形式が変わると毎ターン5秒（直列に実行される場合は最大65秒）遅れる。`timeoutSec` を指定していないagentStop hookが既定のタイムアウトに近づく。

- 対応案：一致する `hook.start` がなくても、入力の `timestamp` 以降のagentStopの `hook.start` があれば書き込み済みとみなす。実機で一致することを確認した記録をREADMEに残す。

### 11. block-commands：引用符の中の誤拒否（低）

`git commit -m "fix reset; git reset is bad"` を `git reset` として拒否する。拒否は毎回行われるため、メッセージを書き換えるしかない。

### 12. simulate-edit-tool-changes：引数名の対応のずれ（低）

[extract-edit-tool-changes.py](./.github/hooks/helpers/utilities/extract-edit-tool-changes/extract-edit-tool-changes.py) は `old_str`・`old_string`、`new_str`・`new_string` の両方を読むが、[simulate-edit-tool-changes.py](./.github/hooks/helpers/utilities/simulate-edit-tool-changes/simulate-edit-tool-changes.py) は `old_str`・`new_str` だけを読む。`old_string` で渡された編集は、編集後の内容を検査するhookが黙って対象外にする。パッチの `*** Add File:` などの正規表現も2つのutilityに同じものがある。

- 対応案：引数名とパッチの正規表現を一方にまとめる。Copilotが `old_string` を使わないなら、extract側から外す。

### 13. 状態ファイルの直接上書き（低）

[request-adversarial-review.py](./.github/hooks/helpers/events/agentStop/request-adversarial-review/request-adversarial-review.py) は一時ファイルに書いてから `os.replace` で置き換えるが、`set-debug-mode`・`set-adversarial-review-state`・`hook-change-request-state` の `save_permitted` は `open(..., 'w')` で直接書く。書き込みの途中に読まれると、読む側（`block-hook-tampering` など）がJSONの解析に失敗して異常終了し、ツールの実行が拒否される。イベントが異なるため重なりにくいが、書き方を揃えたい。

### 14. run-hook：UUIDでない `sessionId` での標準エラー（低）

[README](./.github/hooks/helpers/runner/README.md) は「`sessionId` を読めない入力のときは、記録だけを行わない」とするが、文字列ではあるがUUIDでない `sessionId` では `get_state_path` が `ValueError` を出し、全hookが `Failed to record hook run for …` を標準エラーに出す。終了コードは変わらない。

- 対応案：`_get_session_id` でUUIDとして解析できない値も `None` にする。

### 15. clean-hook-temp-files：スクリプト本体のテストがない（低）

削除の処理（`hook-temp-files.py`）は `test-check-hook-temp-files.py` が検証しているが、[clean-hook-temp-files.py](./.github/skills/clean-hook-temp-files/scripts/clean-hook-temp-files.py) の `main`（`--dry-run` の集計、削除失敗時の終了コード2）にはテストがない。`test-all-hooks.py` の探索対象（`helpers` 配下）にも入っていない。

## 問題がなかった点

- 入力の検証：全hookが空・非JSON・項目の欠落・型違いを終了コード2と理由つきで処理し、トレースバックを出さない。
- 状態の更新：SQLiteは `BEGIN IMMEDIATE` と3秒のロック待ちで、並列のツール呼び出しでも重複して拒否・通知しない。
- ランナー：プロセス内での実行、標準入出力の差し替えと復元、終了コードの扱いがREADMEどおり。記録の失敗はhookの判定を変えない。
- 差し戻しの回数：debug-add-response-footer（No.9）を除くagentStop hookは、同じ依頼での差し戻しを種類ごとに1回に限っている。
- 対応関係：定義JSONの並び順、hookのフォルダー、README、発動フローの表、3か所のskill（`.github`・`.claude`・`.agents`）がすべて一致している。
- CI：3つのOSで、定義の検証・テスト出力の検証・一括テストの3本を実行している。
