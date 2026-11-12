# hookの冗長・誤りの調査結果

調査日：2026-11-12

## 調査の範囲と方法

- 対象は `.github/hooks/` 配下の全hook（共通59個、リポジトリ専用4個）、hookランナー、utility（29個）、hook定義JSON、各README、`.github/README.md`・`.github/-repo-README.md` の発動フロー。
- 実行スクリプト・定義JSON・READMEは全件を読んだ。テスト（`test-*.py`、約18,500行）は構造の確認にとどめ、1行ずつは読んでいない。
- 「誤り」のうち再現できるものは、関数を直接呼ぶ小さな実験で確かめた（各項目に「検証」と記載）。コードは変更していない。
- 実行環境はLinux・Python 3.13.18。Windows固有の指摘は実機で確かめていない（該当項目に明記）。
- パスは `.github/hooks/` からの相対で示す。`events/` は `helpers/events/`、`-repo-events/` は `helpers/-repo-events/`、`utilities/` は `helpers/utilities/` の略。

### テストの実行結果

`python3 .github/hooks/tests/test-all-hooks.py` は94ファイル中93が成功、1が失敗。

- 失敗：`events/preToolUse/block-commands/test-block-commands.py` の `test_wrappers_and_paths`（`entry_point='sh'`）。`block-commands.sh` はPython 3.14未満で全コマンドを拒否するため、3.13では通らない。hookの不具合ではなく環境起因。ただし、このテストは3.14未満でスキップしない（`block-py314-incompatible-syntax` などのテストはスキップしている）。

## サマリー

|区分|件数|主な内容|
|--|--|--|
|A. 動作の誤り|12|判定漏れ・誤拒否・異常終了・OS依存の不具合|
|B. 記述の誤り|17|事実と異なるコメント、README・コメントの食い違い、誤字|
|C. 冗長（横断）|14|同じ処理・定数の複製、utilityの再実装|
|D. 冗長（個別）|18|不要な処理、到達しない分岐、過剰な一般化|
|E. 規約との不整合・表記ゆれ|12|共通規約からの逸脱、hook間の流儀の違い|

## A. 動作の誤り

重要度は、影響の大きさと起きやすさから付けた（高・中・低）。

### A-1（高）detect-unrequested-changes：他のhookの差し戻しを「ユーザーの依頼」として読む

- 場所：`events/agentStop/detect-unrequested-changes/detect-unrequested-changes.py:87-89`
- 内容：依頼の文として、依頼の範囲にあるすべての `user.message` のうち、自身の差し戻し（`_REASON_PREFIX` で始まるもの）以外を集める。他のagentStop hookの差し戻しも `delivery: "queued"` の `user.message` なので、依頼の文に混ざる。差し戻しの文には `remove`・`delete`・`format` などの語や、問題のファイルのパスが含まれるため、「削除・整形を依頼した」「ファイルに言及した」と判定され、検出が消える。
- 検証：`app.py` の修正の依頼で `legacy/old_module.py` を `apply_patch` で削除する履歴を作ると、2つの理由（依頼にない削除、言及のないファイル）で検出する。同じ履歴に、detect-phantom-references の差し戻し（そのパスを列挙し、`correct or remove the wrong references` を含む）を1件足すと、検出は0件になる。
- 補足：READMEは「割り込みで追加された指示は依頼の文に含める」とだけ書いており、hookの差し戻しが混ざることは書いていない。agentStop hookは15個あり、1つの依頼で複数が差し戻すのは普通に起きる。
- 修正案：既知の差し戻しの書き出し（各hookの `_REASON_PREFIX`）で始まる `queued` のメッセージを除くか、依頼の文は先頭の `user.message` と、hookの差し戻しでない割り込みに限る。

### A-2（高）preserve-line-endings：Windowsで、大文字を含む名前のファイルを検査しない（実機未確認）

- 場所：`events/postToolUse/preserve-line-endings/preserve-line-endings.py:105`（`os.path.normcase`）→ `:45`（``f'HEAD:./{os.path.basename(path)}'``）
- 内容：パスを `os.path.normcase` で正規化してから、そのファイル名で `git cat-file --filters HEAD:./<名前>` を実行する。Windowsの `normcase` は小文字にするため、`README.md` は `readme.md` として問い合わせることになる。gitのツリー内のパスは大文字小文字を区別するので見つからず、`None`（比較対象なし）として黙って対象外になる。C#のファイル（`Program.cs` など）はほぼすべて該当する。
- 検証：Linuxで同じ関数に `.github/README.md` を渡すとHEADの内容を返し、`.github/readme.md` を渡すと `None` を返す。Windowsの `normcase` が小文字にすることと合わせた推論で、Windowsでの実行は確かめていない。
- 修正案：`normcase` は状態のキー（SHA-256）を作るときだけに使い、gitとファイルの読み込みには正規化前のパスを使う（`block-generated-file-edits.py:110-115` と同じやり方）。

### A-3（中）check-test-runs：`pipefail` の語があるだけで「終了コードがテストの結果を表す」と判定する

- 場所：`utilities/check-test-runs/check-test-runs.py:29`（`_PIPEFAIL_PATTERN`）、`:117`
- 内容：コマンドのどこかに `pipefail` か `PIPESTATUS` の語があれば、パイプの途中のテストも結果が伝わるとみなす。`set +o pipefail`（無効化）や、引数に書いただけの語でも有効扱いになる。verify-test-pass-claims と require-hook-tests が、失敗を隠したテストを成功の根拠として受け入れる。
- 検証：`is_test_result_reported('set +o pipefail; pytest | tail -5')` と `is_test_result_reported('echo pipefail; pytest | tail -5')` がどちらも `True` を返す。
- 補足：block-masked-exit-codes は `set -o`・`set +o` の順序を追って正しく判定している（`block-masked-exit-codes.py:76-84`）。同じ判定が2通りあり、結果が食い違う。
- 修正案：block-masked-exit-codes の判定をutilityに移し、両方から使う。

### A-4（中）debug-add-response-footer：他のhookの差し戻しでフッターの集計範囲が切れる

- 場所：`events/agentStop/debug-add-response-footer/get-used-hooks.py:29-33`、`get-used-skills.py:25-29`、`get-fired-hooks.py:47-54`
- 内容：3つとも、フッター修正の依頼で始まらない `user.message` をすべて「新しい依頼」として集計をやり直す。他のagentStop hookの差し戻し（`queued`）も区切りになるため、差し戻しの後は、同じ依頼でそれまでに使ったskill・起動したhookがフッターから消える。get-turn-events utility（`delivery` が `queued` でないものを区切りとする）と、同じhookの `_count_corrections`（utilityを使う）とは区切り方が違う。
- 補足：READMEの「ターンの区切り」は、フッター修正の依頼を区切りとしないことだけを書いている。
- 修正案：3つとも get-turn-events の区切りを使う。

### A-5（中）get-used-skills：引数がJSON文字列だとフッターのhookが異常終了する

- 場所：`events/agentStop/debug-add-response-footer/get-used-skills.py:35-37`
- 内容：`tool.execution_start` の `arguments` が辞書でなければ `ValueError` を送出し、hookは終了コード2で終わる。get-turn-events は「引数は記録されたまま返す（JSON文字列の場合もある）」と明記しており（`get-turn-events.py:62`）、文字列で記録された場合に落ちる。
- 検証：`arguments` をJSON文字列にした履歴を渡すと `ValueError: Skill tool arguments must be a JSON object.` になる。
- 補足：実際の履歴で文字列になる頻度は確かめていない。A-6と合わせ、トランスクリプトの `arguments` の扱いが4通りある（E-3）。

### A-6（中）detect-unmeasured-performance-claims：引数がJSON文字列だと計測を見落とす

- 場所：`events/agentStop/detect-unmeasured-performance-claims/detect-unmeasured-performance-claims.py:62`
- 内容：`arguments` が辞書のときだけ `command` を読む。文字列で記録された計測コマンドは無かったことになり、計測済みの主張を誤って差し戻す。
- 検証：`hyperfine ./a` を、辞書の引数で記録すると成功したコマンドに入り、JSON文字列で記録すると入らない。
- 修正案：`unwrap-tool-args` を通す（require-hook-tests は通している）。

### A-7（中）block-hook-tampering：読むだけの `open(...)` を書き込みとして拒否する

- 場所：`events/preToolUse/block-hook-tampering/block-hook-tampering.py:86`（``open\([^)]*[\'"][wax+]``）
- 内容：`open(` の括弧内に、`w`・`a`・`x`・`+` で始まる文字列引数があれば書き込みとみなす。モードではなく、`encoding='ascii'` や `a` で始まるファイル名でも一致する。
- 検証：``python3 -c "…json.load(open('.github/hooks/agentStop.json', encoding='ascii'))"`` を変更とみなす（`cat .github/hooks/agentStop.json` はみなさない）。
- 補足：このhookは再実行しても通らない（ユーザーがhookに触れるまで拒否し続ける）ため、誤拒否の影響が他のhookより大きい。READMEの「変更しない操作も拒否する」の例（`cp` など）には含まれていない。
- 修正案：2番目以降の引数か `mode=` に限って、モードの文字列（`^[rwaxbt+]{1,3}$` のうち書き込みを含むもの）と照合する。

### A-8（中）gitの呼び出しの合計時間が `timeoutSec`（10秒）を超えうる

- 場所：`block-regression-of-past-fixes.py:27`（4秒 ×（blame＋show）× ファイル数）、`detect-rename-leftovers.py:44`（3秒 ×（ルートの特定＋名前の数））、`preserve-line-endings.py:30`（5秒 × ファイル数）、`check-git-state.py:18`（5秒 × 最大4回）、`block-exclusion-escapes.py:43`（5秒 × ディレクトリ数）
- 内容：呼び出し1回ごとの上限だけがあり、合計の上限がない。複数ファイルのパッチや重いリポジトリでは、hook全体がCopilotの `timeoutSec` で打ち切られる。打ち切られたときの扱い（許可か拒否か）はhookからは決められない。
- 補足：block-commit-message-mismatch だけは、全体の期限（7秒）を設けて残り時間を各呼び出しに渡している（`block-commit-message-mismatch.py:62,223-228`）。
- 修正案：同じ期限の仕組みを run-git utilityに持たせる。

### A-9（低）check-python.cmd・block-commands.cmd：負の終了コードをPythonありとみなす

- 場所：`events/sessionStart/check-python/check-python.cmd:6`、`events/preToolUse/block-commands/block-commands.cmd:6`（どちらも `if %errorlevel% geq 1`）
- 内容：Pythonが異常終了して負の終了コード（`0xC0000135` など）を返すと、条件を満たさず「3.14以降が使える」と判定する。check-python では案内が出ない。block-commands では判定をすり抜けて `python block-commands.py` へ進むため、そこでも失敗した場合にだけ拒否になる。
- 修正案：`if not %errorlevel%==0` にする。Windowsの実機では確かめていない。

### A-10（低）check-test-runs：「まず」を否定とみなし、成功の主張を見落とす

- 場所：`utilities/check-test-runs/check-test-runs.py:34`（`_JAPANESE_NEGATION` の `ず`）
- 内容：成功の語の直後6文字以内に `ず` があれば否定とみなす。「まず」「必ず」も一致する。
- 検証：`claims_tests_passed('テストは成功し、まず問題ない。')` が `False`（`'テストは成功した。'` は `True`）。
- 補足：同じ正規表現が `block-commit-message-mismatch.py:53` に複製されており、「テストを追加し、まず…」でも同じことが起きる。

### A-11（低）定義ファイルの誤検知・検知漏れ

- `events/preToolUse/block-error-swallowing/error-swallowing-definitions.json:28`（ignored command failure）：`\|\|\s*(?:true|:)\s*(?:[;)]|$)` がシェル以外のコードにも一致する。検証：`const enabled = options.flag || true;` に一致する。文書以外の全ファイルが対象のため、JavaScript・C#で誤拒否する（1回だけ）。
- `events/postToolUse/record-verified-packages/record-verified-packages.py:43`：`\b404\b` がバージョン番号にも一致する。検証：`left-pad@1.404.0` に一致する。出力にそのようなバージョンがあると、実在を確かめた記録を残さない。
- `events/agentStop/detect-phantom-references/detect-phantom-references.py:138`：`relative_path.startswith('..')` は `..foo/x` のような名前でも真になる。`== '..'` か `'../'` で始まるかで判定すべき。
- `events/postToolUse/detect-rename-leftovers/detect-rename-leftovers.py:35`：修飾子の一覧に `new` があるため、行頭の `new Foo(` を定義とみなす。

### A-12（低）Windowsで表示パスが小文字になる（実機未確認）

- 場所：`preserve-line-endings.py:135`、`detect-edit-oscillation.py:140`、`block-elided-rewrites.py:109-110`、`block-secret-writes.py:107-108`、`require-read-before-edit.py:80-81`
- 内容：`normcase` 済みのパスから表示パスを作るため、Windowsでは理由に示すパスがすべて小文字になる。判定には影響しない。他のhookは、表示とパターン照合には正規化前のパスを使い、`normcase` はキーにだけ使っている。

## B. 記述の誤り

### B-1 事実と異なるコメント

- 「引用符・バックスラッシュ・改行など、JSONとして必要なエスケープは `ensure_ascii=False` で行われる」（4か所：`check-target-session.py:48`、`load-copilot-instructions.py:49`、`block-commands.py:39`、`debug-add-response-footer.py:164`）。エスケープは `json.dumps` が常に行う。`ensure_ascii=False` は非ASCII文字を `\uXXXX` にしない指定で、エスケープとは関係がない。
- 「標準入力パイプにツール名や引数を含むイベント情報を…」（`check-target-session.py:25-26`、`set-debug-mode.py:29-30`、`debug-add-response-footer.py:92-93`）。sessionStart・userPromptSubmitted・agentStopの入力にツール名と引数はない。preToolUseの `block-commands.py:137-138` から写したコメントが残っている。
- `utilities/read-hook-input/read-hook-input.py:5`「すべてのhookが共有する」。check-hook-temp-files と load-copilot-instructions は標準入力を読まず、check-python は `.sh`・`.cmd` だけで動く。
- `events/preToolUse/block-commands/block-commands.py:52-54`：引数名は `definitions_path` だが、中身は `['--definitions-path', 'rules.json']` のような引数のリスト。コメント自身が「パス単体ではなく…リストを受け取る」「現行テストでは未使用」と書いている。他のhookは `main(arguments=None)`。

### B-2 コメントとコード・READMEの食い違い

- `events/postToolUse/detect-stale-parameter-docs/detect-stale-parameter-docs.py:22-25`：読み取る形式のコメントが正規表現より古い（`Params`・`Keyword Args`・`Keyword Arguments`・`Other Parameters` がない）。READMEにも `Keyword Arguments:` がない（`:27` の正規表現にはある）。
- `utilities/viewed-commits-state/viewed-commits-state.py:13`「メッセージと差分を読んだコミット」。record-viewed-commits のREADMEと実装は「メッセージを読んだ」（既定の `git log` は差分を出さない）。
- `events/postToolUse/record-verified-packages/record-verified-packages.py:37`：正規表現は `npm v <name>`、`yarn view`・`yarn show`、`pnpm info`・`pnpm show` も受け付けるが、READMEの表にない。
- `events/userPromptSubmitted/record-hook-change-requests/record-hook-change-requests.py:92`「1の順に対応して」。READMEは「1,2の順に対応して」。
- `utilities/wait-for-agent-stop-event/wait-for-agent-stop-event.py:21-24,53,61`：コメントとREADMEは「timestampが一致するものを対象とし、一致しなくても時刻で…」と一致を主に書くが、コードは時刻の判定を先に行う。時刻の判定は一致する場合をほぼ含むため、一致の分岐（`:61`）は、イベントの時刻を読めないときにしか働かない。
- 古い名前のままのコメント：`block-commands.py:46`（`write_stdout_deny`）、`:119`（`is_non_empty_string`）、`block-shell-file-edits.py:116`（`tokenize_bash`）。いずれも `_` 付きに改名済み。

### B-3 READMEの誤り・不足

- 編集ツールのパス引数を `path` だけと書いている（`create` は `file_path`）：check-cross-agent-skill-wrappers・detect-edit-oscillation・preserve-line-endings・block-generated-file-edits・require-read-before-edit の各README。record-seen-files・block-hook-tampering・block-nonportable-paths のREADMEは正しく書いている。
- 「検出する内容」の表に、検出対象ではない行が混ざっている：`対象外のファイル`（block-error-swallowing・block-invisible-characters・block-native-path-separators・detect-temporary-code・detect-unrequested-changes）、`残る参照の例`（detect-rename-leftovers）。共通規約の項目5は、対象外を `## 例外` に分けるよう定めている。
- request-adversarial-review のREADMEに、サブエージェントの停止を対象外とする記述がない（コードは `:56` で除外）。detect-repeated-failures のREADMEにも `agentId` の除外がない（コードは `:83`）。
- set-debug-mode は空白だけのプロンプトで異常終了する（`set-debug-mode.py:39-40`）が、READMEに記述がない。同じ形の set-adversarial-review-state は異常終了しない。

### B-4 誤字

- `events/sessionStart/check-python/README.md:12`「ターミナルを再起動を推奨する」（「を」の重複）。
- `events/preToolUse/block-commands/block-commands.py:134`「turple」（tuple）。
- `events/userPromptSubmitted/set-adversarial-review-state/set-adversarial-review-state.py:55`「pending；」（全角のセミコロン。他は「：」）。

## C. 冗長（横断）

共通規約の項目3は「既存のutilityと同じ処理は再実装せず、`runpy.run_path` で読み込んで使う」と定めている。次の重複は、utilityにまとめられる。件数はテスト以外の `.py` を数えた。

### C-1 拒否・差し戻しの出力

- 拒否のJSON（`permissionDecision: deny`）の組み立てが26ファイル、差し戻しのJSON（`decision: block`）が14ファイルにある。理由を標準エラーにも出すかどうか、`separators` を付けるかどうかがhookごとに違う（E-4）。

### C-2 定義ファイルの読み込み

- `_load_definitions` に当たる関数が23ファイル、`--definitions-path` の `argparse` が24ファイルにある。「空でない文字列の配列か」の検査は、関数にしたものが7つ（`_is_non_empty_string_list` が4つ、`_is_string_list` が3つで、`allow_empty` の既定値が3通り）あり、残りはインラインで書いている。
- 完全に同じもの：`_is_non_empty_string_list` と読み込みの前半（ファイルの確認、JSONの解析、オブジェクトかの検査）が、detect-dropped-requirements（`:47-62`）・detect-sycophantic-reversal（`:40-55`）・detect-unread-speculation（`:47-62`）で一致する。
- `{name, pattern}` の配列を読む処理が、block-secret-writes（`:40`）・block-elided-rewrites（`:48`）・block-error-swallowing・block-invisible-characters・block-mojibake-writes・block-test-weakening・block-native-path-separators・detect-temporary-code（`_load_named_patterns`、`:49`）にある。

### C-3 ほぼ同じ骨格のhook（5つ）

- block-error-swallowing・block-invisible-characters・block-mojibake-writes・block-native-newlines・block-native-path-separators は、定数（`_MAX_SNIPPETS = 3`、`_MAX_SNIPPET_LENGTH = 80`、`_FINDING` の形）、`_load_definitions`、`_format_snippet`、`main` のループがほぼ同じ。違いは照合の中身だけ。
- `_find_invisible_characters`（`block-invisible-characters.py:90-103`）と `_find_mojibake`（`block-mojibake-writes.py:75-88`）は、行の前後から除く文字だけが違う。

### C-4 「新たに書き込む行のうち、パターンに一致し、既存の内容にないもの」

- 7か所にある：`block-elided-rewrites.py:64`、`block-error-swallowing.py:80`、`block-invisible-characters.py:90`、`block-mojibake-writes.py:75`、`block-test-weakening.py:78`、`block-native-path-separators.py:95`、`detect-orphaned-additions.py:140-179`。
- 同じ目的のutility `find-introduced-lines-by-regex` は、detect-temporary-code だけが使っている。一致した位置から行を取り出す `_get_line` も3か所にある（utilityの `:25-28`、`block-native-newlines.py:137-140`、`detect-orphaned-additions.py:167-169`）。

### C-5 SQLiteの定型処理

- `os.makedirs` → `closing(sqlite3.connect(..., timeout=3))` → `CREATE TABLE IF NOT EXISTS` → `BEGIN IMMEDIATE` と、同じ2行のコメントの組が9ファイルにある（assertion-failure-values-state・known-files-state・verified-packages-state・viewed-commits-state・filter-first-time-denials・filter-new-notifications の各utility、detect-repeated-failures・detect-edit-oscillation・detect-vacuous-test-success）。
- コメント「hookは呼び出しごとに別プロセスなので、状態をセッションUUID別に保存する。」が、get-state-path と、それを包む各utility・hookに繰り返されている。

### C-6 ファイルのキー（パスのSHA-256）

- `hashlib.sha256(...)` のインライン記述が20ファイルにある。`get_file_key` は2つあり、`normcase` を中で行うもの（`assertion-failure-values-state.py:50`）と行わないもの（`known-files-state.py:34`）に分かれる。hook側も、`normcase(realpath(...))` したパスを渡すものと、`normcase` だけのものがある。
- `_record_denial` は `block-exclusion-escapes.py:204-208` と `block-time-bomb-literals.py:133-137` で一字一句同じ。

### C-7 ツール名の集合

- `_EDIT_TOOL_NAMES` の定義が12ファイル、`_SHELL_TOOL_NAMES`（または `_SHELL_TOOL_PATTERN`）が12ファイルにある。編集ツールの集合は `extract-edit-tool-changes.py:19` にもあり、hook定義JSONの `matcher` とも重なる。型も `set`・`frozenset`・大文字小文字を区別しない正規表現の3通り（E-5）。

### C-8 シェルコマンドの分割

- 分け方が8通りある。`shlex` によるもの：`check-test-runs._get_simple_commands`、`block-masked-exit-codes._get_pipelines`、`block-shell-file-edits._tokenize_bash`、`block-commit-message-mismatch._split_segments`。正規表現によるもの：`block-hook-tampering.py:29-30`、`block-snapshot-rubber-stamp.py:30`、`record-verified-packages.py:27`（前の2つは同じ正規表現）、block-commands の定義JSON。
- リダイレクトの演算子の集合：`check-test-runs.py:25` と `block-masked-exit-codes.py:32` が同一、`block-shell-file-edits.py:35,37` は同じ12個を2つに分けたもの。
- 読み飛ばす前置きのコマンド（`sudo`・`env` など）の一覧が4つあり、中身が少しずつ違う（`check-test-runs.py:23`、`block-shell-file-edits.py:39`、`block-hook-tampering.py:36`、block-commands の定義JSON）。
- 演算子の文字の集合が4つある（`split-bash-lines.py:10`、`check-test-runs.py:26`、`block-shell-file-edits.py:38`、`block-masked-exit-codes.py:64` のインライン）。

### C-9 応答の文の解析（agentStop）

- `_FENCED_CODE_PATTERN`（同じ正規表現）が8ファイル：check-test-runs・detect-dropped-requirements・detect-empty-promises・detect-phantom-references・detect-sycophantic-reversal・detect-unmeasured-performance-claims・detect-unread-speculation・verify-test-pass-claims。
- `_INLINE_CODE_PATTERN` が4ファイル、`_QUOTED_PATTERN` が2ファイル（`check-test-runs.py:41`、`detect-empty-promises.py:45`）、`_QUOTE_LINE_PATTERN` が2ファイル、`_JAPANESE_NEGATION` が2ファイル（`check-test-runs.py:34`、`block-commit-message-mismatch.py:53`）。
- 文の区切りの正規表現が3通り（`detect-empty-promises.py:42`、`detect-unmeasured-performance-claims.py:26`、`detect-unread-speculation.py:36`）。
- detect-phantom-references と detect-unread-speculation は、`_MARKDOWN_LINK_PATTERN`・`_NON_PATH_CHARACTERS`・`_EXTENSION_PATTERN` が同一で、パスの候補の抽出（`:41-54` と `:92-107`）と、cwd・リポジトリルートでの解決（`:129-132` と `:125-128`）もほぼ同じ。

### C-10 依頼の範囲の編集の収集（agentStop）

- 「最新の依頼の `tool.execution_start` から編集の変更を集める」処理が3か所にある（`detect-lockfile-drift.py:77-89`、`detect-orphaned-additions.py:90-103`、`detect-temporary-code.py:89-102`）。get-turn-events に置ける。
- `debug-add-response-footer.py:25-31` の `_count_corrections` は、get-turn-events の `is_already_requested` と同じ条件を数えるだけの変形。

### C-11 期待値・実際の値のhook（2つ）

- block-goalpost-moving と block-hardcoding-for-tests は、`_LITERAL_PATTERN`（`:25-28` と `:27-30`、コメントも「同じ基準」と書いている）、`_shorten`・`_MAX_DISPLAY_LENGTH = 40`、`main` の骨格が同じ。共有先は assertion-failure-values-state が自然。

### C-12 表示用の切り詰め

- `_shorten`・`_format_snippet`・`_format_command`・`_shorten_command` が12ファイルにある。上限は40・60・80・120・200とばらつく。

### C-13 定数の複製

- 文書の拡張子（`md`・`markdown`・`rst`・`txt`・`adoc`）：定義JSONが6つ（error-swallowing・invisible-character・timeout-inflation・path-separator・temporary-code・orphaned-addition）、`.py` が2つ（`block-hardcoding-for-tests.py:25`、`verify-test-pass-claims.py:30`）。
- エコシステムの名前（`nuget`・`pypi`・`npm`・`python-module`）：`verified-packages-state.py:22-25`（`_` 付きで外から使えない）、`record-verified-packages.py:21-24`、`block-unverified-dependencies.py:54-57`。
- `.github/hooks` を指す正規表現：`block-hook-tampering.py:24`、`require-hook-tests.py:26`、`verify-test-pass-claims.py:32`。
- コメント行の接頭辞：`block-time-bomb-literals.py:52`、`block-timeout-inflation.py:38`。
- ANSIエスケープの正規表現：`detect-vacuous-test-success.py:24` と `record-viewed-commits.py:24`（中身も違う）。
- `toolResult.textResultForLlm` を取り出す3行：detect-vacuous-test-success（`:223`）・record-test-failure-values（`:110`）・record-verified-packages（`:112`）・record-viewed-commits（`:49`）。
- 括弧と引用符を数えて区切る走査が5つ：`detect-stale-parameter-docs.py:152`、`block-tautological-tests.py:91,123`、`block-exclusion-escapes.py:90`、`block-native-newlines.py:88`。
- `block-nonportable-paths.py:25,64-66` は、`extract-edit-tool-changes` の `PATCH_FILE_PATTERN`・`PATCH_MOVE_PATTERN` と同じものを独自に持つ（末尾の空白の扱いが少し違う）。`:70` の `('view', 'undo_edit')` は `NON_WRITE_COMMANDS` の複製。`record-seen-files.py:31` も、get-edited-paths の `patch_operations` で足りるパターンを独自に持つ。

### C-14 そのほかの横断的な重複

- 共通指示の読み込み（存在の確認、読み込み、空の確認と、同じ3つのエラーメッセージ）：`load-copilot-instructions.py:20-45` と `give-subagents-instructions.py:37-54`。
- set-debug-mode と set-adversarial-review-state は、状態の内容以外ほぼ同じ。
- `blocked-command-definitions.json`：4つの定義すべてに、コマンドの始まりとgitのグローバルオプションを表す同じ正規表現（約600文字）が入っており、違うのは末尾のサブコマンドだけ。先頭の `[;&|(){}`\n]` の `\n` は、`(?m)` の `^` と重なる。
- READMEの「check-python-version.pyの呼び出しを `call python …` とする理由」の節が、check-python と block-commands にほぼ同じ文で載っている。
- テスト：補助関数が各テストファイルに複製されている（`__invoke` が31ファイル、`__assert_denied`・`__assert_allowed` が25、`__write_file` が26、`__new_session` が20、トランスクリプトを作る `_user` が13、`_assistant` が12、`_agent_stop_start` が11）。`tests/helpers/` にあるのは `hook-entry.py` と `hook-test-output.py` だけ。

## D. 冗長（個別）

- `events/postToolUse/record-verified-packages/record-verified-packages.py:56-63`（`_strip_version`）：名前を取り出す正規表現（`_NAME`、URLのパターン）が `@1.2.3` や `==1.2.3` を含めないため、実質何もしない。検証：関数を恒等にしても、テスト6件がすべて通る。
- `events/preToolUse/block-unverified-dependencies/block-unverified-dependencies.py:155-158`（`_normalize_name`）：`verified-packages-state.normalize_name` が、対象外のエコシステムの名前をそのまま返すため、包む必要がない。
- `events/preToolUse/block-masked-exit-codes/block-masked-exit-codes.py:127-141`：コマンドは1件なのに `masked`・`command_keys` をリストで持ち回している。`toolCalls` 配列を回していたころの名残とみられる。
- `utilities/check-test-runs/check-test-runs.py:25`、`block-masked-exit-codes.py:32`、`block-shell-file-edits.py:37` の `'<<-'`：`shlex` は `-` を演算子の文字として扱わないため、この字句は現れない（`split-bash-lines.py:66` のコメントも「`-` は区切り語の字句に付く」と書いている）。
- `events/userPromptTransformed/debug-log-transformed-prompt/debug-log-transformed-prompt.py:112` と `debug-show-prompt-log-paths.py:44` の `except (…, SyntaxError, KeyError)`：使う項目は事前に検査しており、到達しない。`debug-add-response-footer.py:113` も、utilityの読み込みだけを `SyntaxError`・`KeyError` で囲っている（他のhookは囲っていない）。
- `events/agentStop/debug-add-response-footer/debug-add-response-footer.py:125-136`：`get_debug_mode` の例外を4つの `except` に分け、別々の文を出している。他のhookは1つにまとめている。
- 同hook：トランスクリプトを1回の実行で約6回読む（待機、`get_turns` が `:28` と `:37` の2回、get-used-hooks、get-fired-hooks、get-used-skills）。`os.path.isfile(transcript_path)` も、`get_transcript_path` で確かめた後に3か所で再確認している（`get-used-hooks.py:14`、`get-used-skills.py:13`、`get-fired-hooks.py:31`）。
- 同hook：見出しの文言を、`format-response-footer.py:2-8` の定数と `check-response-footer.py:7-13` の正規表現の両方に書いている。`format-response-footer.py:1,3,5,7` のコメントは、直後の定数の値を繰り返しているだけ。差し戻しの書き出しの文も4か所にある（`debug-add-response-footer.py:19`、`get-fired-hooks.py:13`、`get-used-hooks.py:31`、`get-used-skills.py:27`）。
- `events/agentStop/debug-show-prompt-log-paths/get-prompt-log-paths.py:16`：解析済みのUUIDを文字列に戻して再び解析している。
- `events/preToolUse/block-commands/block-commands.py:82-89`：`os.path.exists` と `os.path.isfile` を続けて確かめている（後者だけで足りる）。`:126` は空白だけの行。
- `events/sessionStart/check-python/check-python.sh:26`・`check-python.cmd:14`：`if` の中の `exit 0` は、直後の `exit 0` と重なる。`block-commands.sh:19` の `exit $?` も、最後のコマンドの終了コードをそのまま返すだけ。
- `events/sessionStart/check-hook-temp-files/hook-temp-files.py:36-41`：1ファイルにつき `lstat` を2回呼ぶ。`check-hook-temp-files.py:13` の「48 hours」は `_RETENTION_SECONDS` の値を文に書き直したもの。
- `events/postToolUseFailure/detect-repeated-failures/detect-repeated-failures.py:43`（`CHECK (count BETWEEN 1 AND 3)`）と `:25`（「three times within five minutes」）：`_FAILURE_THRESHOLD`・`_WINDOW_SECONDS` の値を書き直している。
- `events/postToolUse/detect-vacuous-test-success/detect-vacuous-test-success.py:154`：ANSIエスケープの除去を、ループの中でパターンごとにやり直している。
- `events/postToolUse/detect-stale-parameter-docs/detect-stale-parameter-docs.py:120-122,220-222`：行の出現回数を数える同じ4行が2か所にある。
- `events/preToolUse/block-tautological-tests/block-tautological-tests.py:184-185`：行ごとのループの中で `re.compile` を呼んでいる。`block-timeout-inflation.py:101` は `_split_numbers(old_line)` を候補ごとに2回評価する。
- `events/preToolUse/block-exclusion-escapes/block-exclusion-escapes.py:168`：`entries.extend(line for line in text.splitlines())` は `entries.extend(text.splitlines())` で足りる。`block-time-bomb-literals.py:197` の `if candidates:` は、直後の `for` と重なる。`block-secret-writes.py:28` の `re.IGNORECASE` は、`:60` で小文字にした名前に使うため不要。
- 1つのhookしか使わないutility：`find-introduced-lines-by-regex`（detect-temporary-code だけ）、`get-current-jst`（debug-add-response-footer だけ）。共通規約の項目3は、utilityを「複数hookで共有する補助スクリプト」としている。前者はC-4の重複をまとめる先にできる。

## E. 規約との不整合・表記ゆれ

### E-1 共通規約（`.github/skills/create-hook/references/hook-conventions.md`）からの逸脱

- 項目1（`hook.start` の `input` を入力仕様の根拠にしない）：`get-prompt-log-paths.py:28-36,47` は、`hook.start` の `input` の `sessionId`・`agentId`・`timestamp` を読んで対象のログを決める。形式が変わると、保存先を表示しなくなる。
- 項目3（utilityを再実装しない）：`request-adversarial-review.py:122-128` は、run-git を使わず `subprocess.run(['git', 'status', …])` を直接呼ぶ。`verify-test-pass-claims.py:69-73` は、unwrap-tool-args と同じ処理をインラインで書いている。
- 項目7（`os.path.dirname(os.path.abspath(__file__))` を基準にする）：`block-commands.py:73` は `os.path.dirname(__file__)`。
- 項目8（`cwd`・`toolName`・`toolArgs` の検証に read-hook-input を使う）：`block-commands.py:153-156` は `toolName` を独自に検証する。
- 項目8（Unicode escapeには復号した文字をコメントに書く）：`format-response-footer.py:9` の `'​'` にコメントがない。
- 項目8（テキストモードで書くファイルは `newline='\n'`）：`write-json-atomically.py:15` と `debug-log-transformed-prompt.py:68` は `newline=''`。改行を変換しない点は同じだが、規約の文言とは違う。
- 項目9（デバッグ専用hookは、無効なら正常終了する）：`debug-add-response-footer.py:97` は、`agentId` とデバッグモードを確かめる前に `transcriptPath` を検証する。デバッグモードが無効でも、パスが不正なら終了コード2で終わる（debug-show-prompt-log-paths は、有効なときだけ検証する）。

### E-2 Python入門の解説コメント

- 一部のファイルにだけ、言語の基礎を説明するコメントがある（`with`・`enumerate`・`isinstance`・`bool`・f-string・`!r`・「C#の…に相当」・「payloadと呼ぶ」など）。該当：block-commands・check-target-session・load-copilot-instructions・set-debug-mode・set-adversarial-review-state・request-adversarial-review・debug-add-response-footer・check-required-tools・wait-for-agent-stop-event、`check-python.sh`・`block-commands.sh`（`2>&1` の説明が同文）。
- 「このファイルが直接実行されたときだけ main() を呼び出す。」のコメントは9ファイルにだけある。
- 意図した学習用のコメントなら、置く範囲（全ファイルか、入口のファイルだけか）を決めるとよい。B-1の誤ったコメントは、すべてこの種のコメントの複製から出ている。

### E-3 トランスクリプトの `arguments`（JSON文字列の場合）の扱いが4通り

- utilityで外す（`require-hook-tests.py:44`、extract-edit-tool-changes 経由のhook）、インラインで外す（`verify-test-pass-claims.py:69-73`）、外さず無視する（`detect-unmeasured-performance-claims.py:62`、A-6）、例外にする（`get-used-skills.py:36`、A-5）。

### E-4 出力の流儀

- 拒否の理由を標準エラーにも出すhookと、標準出力のJSONだけのhook（block-masked-exit-codes・block-shell-file-edits・require-read-before-edit・block-nonportable-paths）がある。
- `json.dumps` に `separators=(',', ':')` を付けるもの（sessionStart・agentStop・block-commands）と付けないもの（ほかのpreToolUse・postToolUse）がある。
- 対象外のツールで、block-commands は `{}` を出力し、他のhookは何も出力しない。
- `reconfigure` の `errors=` が `replace`・`backslashreplace`・指定なしの3通りある（指定なし：`check-required-tools.py:100-101`、`load-copilot-instructions.py:10`、`give-subagents-instructions.py:19`）。
- 件数の上限で切るとき、「and N more」を付けるhookと付けないhook（detect-lockfile-drift・detect-orphaned-additions・detect-temporary-code・block-py314-incompatible-syntax）がある。

### E-5 ツール名の照合

- 多くのhookは大文字小文字を区別する集合で照合し、block-commands（`:163`）・block-masked-exit-codes（`:27`）・block-shell-file-edits（`:24`）は区別しない正規表現で照合する。

### E-6 agentStop hookが編集を集める基準

- 成功した完了だけを数えるもの（`detect-unrequested-changes.py:100-102`、`detect-empty-promises.py:55-58`、require-hook-tests、verify-test-pass-claims）と、開始イベントを成否によらず数えるもの（`detect-lockfile-drift.py:83`、`detect-orphaned-additions.py:100`、`detect-temporary-code.py:99`）がある。後者は最終的なファイルを確かめるため実害は小さいが、拒否された編集も対象に入る。

### E-7 定義ファイルの読み方

- `main(arguments=None)` で引数を差し替えられるhookと、`dest='defs_path'`・`parse_args()` 固定のhook（block-commands・detect-dropped-requirements・detect-sycophantic-reversal・detect-unread-speculation）がある。
- 読み込みの `encoding` は `utf-8-sig` が大半で、`check-required-tools.py:39` と `request-adversarial-review.py:61` は `utf-8`。

### E-8 状態の持ち方

- デバッグモードと hook-change-requests は、読み書きの形式をutilityにまとめている。敵対的レビューの状態は、書く側（set-adversarial-review-state）と読む側（request-adversarial-review、`:69-99` で形式を手で検証）が、それぞれ形式を知っている。
- `detect-unread-speculation.py:136` は、読むだけなのに `open_state`（表の作成と書き込みのロック）を通る。READMEは「読み取りだけを行う」と書いている。

### E-9 `timeoutSec`

- sessionStartの6個のうち、load-copilot-instructions だけ `timeoutSec` がない。userPromptSubmitted・userPromptTransformed・postToolUseFailure・agentStopには1つもない。agentStopは最大5秒待つ処理と10秒のgit（detect-orphaned-additions）があるため意図的とみられるが、どこにも理由が書かれていない。

### E-10 対象のパスの検証

- 編集ツールのパスが無い・不正な入力で、block-nonportable-paths は異常終了（＝拒否）し、他のhookは対象なしとして通す（`block-nonportable-paths.py:68-75`）。
- `block-hook-tampering.py:145` は `normpath` だけで、シンボリックリンクを解決しない。他の多くのhookは `realpath` を使う。リンク経由のパスは、守る対象として認識しない。
- `detect-lockfile-drift.py:132` と `detect-orphaned-additions.py:149` は、`commonpath` の結果を `normcase` せずに比べる（get-display-path は `normcase` で比べる）。Windowsでcwdとリポジトリルートの大文字小文字の表記が違うと、対象から外れる（実機未確認）。

### E-11 READMEの体裁

- 見出しの直後に空行を置くREADME（check-cross-agent-skill-wrappers・detect-edit-oscillation・detect-vacuous-test-success・preserve-line-endings・record-seen-files・record-test-failure-values・debug-log-transformed-prompt・detect-repeated-failures・block-masked-exit-codes・block-shell-file-edits・require-read-before-edit・block-nonportable-paths・debug-show-prompt-log-paths）と、置かないREADME（その他）がある。
- 「ユーザ」（give-subagents-instructions・set-debug-mode・set-adversarial-review-state のREADME、発動フローの「ユーザへの迎合検知」）と「ユーザー」（その他）が混在する。
- `block-nonportable-paths.py:27,83,112` のコメントは英語。他のhookのコメントは日本語。`check-response-footer.py:5` も英語。
- `block-unverified-dependencies.py:244` のコメントは「フック」。他は「hook」。

### E-12 名前

- `check-response-footer.py:29` の `test_response_footer` は、テストではない関数に `test_` を付けている。
- `check-cross-agent-skill-wrappers.py:87` の `_get_display_path` は、共有utilityの `get_display_path` と同じ名前で、別の処理（要素を `/` でつなぐ）をする。
- `require-hook-tests.py:36` の関数 `_get_turn_events` は、モジュール変数 `_turn_events`（utility）・utility名 `get-turn-events` と紛らわしい。
- Pythonの拡張子の集合がhookごとに違う：`.py`・`.pyi`・`.pyw`（`block-py314-incompatible-syntax.py:27`）、`.py`・`.pyi`（`detect-stale-parameter-docs.py:20`）、`\.pyi?$`（`newline-definitions.json:3`、`block-unverified-dependencies.py:29`）。

## 問題がなかった点

- `.github/README.md`・`.github/-repo-README.md` の発動フロー：全63個のhookが表にあり、mermaidの図の項目はすべて表にあり、幅も全角14文字以内に収まっている。
- hook定義JSON：実行スクリプトの昇順に並び、hookのフォルダーと1対1に対応している。
- utilityのREADMEの「利用するhook」：実際に読み込んでいるhookと一致している（get-edited-paths は、`get_path_argument` だけを使う2つを本文で別に説明している）。

## 確かめていないこと

- Windows・macOSでの実行（A-2・A-9・A-12・E-10の一部は、コードからの推論）。
- 実際のCopilotのトランスクリプトで、`arguments` がJSON文字列になる頻度（A-5・A-6の起きやすさ）。
- `timeoutSec` を超えたときのCopilotの扱い（A-8の結果が許可か拒否か）。
- テストコードの1行ずつの確認（C-14の件数は、関数名の出現数から数えた）。
