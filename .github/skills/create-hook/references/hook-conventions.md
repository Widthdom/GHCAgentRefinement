# hookの共通規約

1. Copilot SDKのイベント、設定、入出力仕様を使い、hookにはSDK以外の形式向けの互換処理、フォールバック、専用の実装・検証を加えない。CodexとClaude Codeでは、各アダプターが入出力をSDKの形式に変換して同じhookを実行するため、hookを実行環境ごとに書き分けない。Windows特有の制御を行うhookを除き、Windows、macOS、Linuxのすべてを対象にする。イベントは対象VS Codeバージョンで使えることを確認する。
   - 入力の項目は[公式の入出力仕様](https://docs.github.com/en/copilot/reference/hooks-reference)で確認する。セッションのイベント履歴（`events.jsonl`）にある `hook.start` の `input` は、hookの標準入力と形式が異なる場合があるため、入力仕様の根拠にしない。
   - preToolUse・postToolUseには、ツール呼び出し1件の名前（`toolName`）と引数（`toolArgs`）が渡される。`toolCalls` 配列は渡されない。
   - アダプターが対応していないイベントやツールを扱うhookは、その実行環境では動かない。対応状況はREADMEではなくアダプターの定義で確認する（Claude Codeは `.github/hooks/helpers/runner/run_claude_hook.py` の `_EVENT_NAMES`・`_TOOL_NAMES`、Codexは `.github/hooks/helpers/runner/run_codex_hook.py` の `_EVENT_NAMES`・`_convert_tool`）。動かす場合だけ、各アダプターのREADME（`.claude/README.md`・`.codex/README.md`）の「変更するとき」の章に従ってアダプターを更新する。
   - 編集ツールの対象のファイルのパスは、ツールによって引数名が異なる（`edit` は `path`、`create` は `file_path`）。`path` を直接読まず、[edit_tool_changes.py](../../../hooks/helpers/utilities/edit-tool-changes/edit_tool_changes.py) の `get_path_argument` で取り出す。
2. hook定義はイベントごとに `.github/hooks/<event-name>.json` の配列にまとめ、要素を実行スクリプトパスの昇順に並べる。
3. hookのファイルは `.github/hooks/helpers/events/<event-name>/<hook-name>/` に置き、実行スクリプトは `<hook_name>.py` とする。複数hookで共有する補助スクリプトは [.github/hooks/helpers/utilities/](../../../hooks/helpers/utilities/) の `<utility-name>/<utility_name>.py` に置き、同じフォルダーにテスト `test_<utility_name>.py` を置く。utilityの処理の規則を複数のhookのREADMEで説明する場合は、同じフォルダーの `README.md` に1回だけ書き、hookのREADMEからはリンクする。既存のutilityと同じ処理は再実装せず、`import` で読み込んで使う。
   - hook名・utility名（フォルダー名）と、`.github/hooks/` 配下に作るPython以外のファイルの名前は、小文字の英単語をハイフンでつないだ形にする。イベント名（`preToolUse` など）と `README.md` はそのまま使う。
   - Pythonのファイル（`.py`）の名前は、モジュールとして `import` できるよう、小文字の英単語をアンダースコアでつないだ形にする。hook・utilityのフォルダーにある実行スクリプトとテストは、hook名・utility名のハイフンをアンダースコアに変えた名前にする（`read-hook-input/read_hook_input.py`・`test_read_hook_input.py`）。
   - 他のフォルダーのモジュールは、そのフォルダーを `sys.path` に加えてから `import` する。フォルダー名はハイフンを含みパッケージにできないため、フォルダーごとに加える。`import` した名前はモジュールの最上位の名前になるため、`import read_hook_input as _hook_input`・`from get_state_path import get_state_path as _get_state_path` のように `_` で始まる別名を付ける（項目14）。他から読み込まれるモジュールを新しいフォルダーに置いた場合は、Pylanceが解決して定義へ移動できるよう、リポジトリ直下の [pyrightconfig.json](../../../../pyrightconfig.json) の `extraPaths` にそのフォルダーを加える。
   - モジュールは同じプロセスで最初に `import` したときに1回だけ実行され、以降は使い回される。呼び出した時点の値（時刻など）はモジュール直下で計算せず、関数にして返す。`runpy.run_path` は、スクリプトを `__main__` として実行する場合（ランナーによるhookの実行）と、テストでスクリプトの実行をやり直す場合にだけ使う。
   - 関数内で必要になったときだけ `import` する場合、読み込みの失敗は `ImportError`（`ModuleNotFoundError` を含む）になる。その `import` を囲む `except` で他の失敗と同じく異常終了として扱うときは、`ImportError` も捕まえる。
4. JSONのコマンドは、hookランナーに実行スクリプトのリポジトリルート相対パスを渡して起動する。`bash` には `python3 .github/hooks/helpers/runner/run_hook.py <実行スクリプト>`、`powershell` には `python .github/hooks/helpers/runner/run_hook.py <実行スクリプト>` を記述する。Pythonが無い環境でも動く必要があるhook（`check-python`、`block-commands`）だけは、ランナーを通さず `.sh`・`.cmd` から起動する。
5. 各hookのフォルダーに `README.md` を作成し、発動条件、処理概要（hookが何をするかの1〜2文の説明）、処理詳細の順に記載する。前提条件、例外（意図して対象外・許可とするもの）、制約と限界（検出漏れ・誤検知・拒否の回数など）がある場合は、処理詳細や備考に混ぜず、それぞれ `## 前提条件`・`## 例外`・`## 制約と限界` の章に分けて、検出内容などの詳細の章の後、背景・備考の前に記載する。設計背景は必要な場合だけ記載する。
6. Python 3.14対応の構文・標準ライブラリAPIを使う。最低対応・推奨は3.14とし、新規インストールには3.14系の最新パッチ版を案内する。
7. 補助ファイルは `os.path.dirname(os.path.abspath(__file__))` を基準に参照する。パスは `os.path.join` で階層ごとに連結し、親は `os.path.dirname`、一時ファイルの基点は `tempfile.gettempdir()` を使う。hookが実行をまたいで保持する状態は `<一時フォルダー>/ghc-agent-refinement/hook-states/<状態名>/` に、[get_state_path.py](../../../hooks/helpers/utilities/get-state-path/get_state_path.py) の `get_state_path` でセッションUUID別のパスを作って置く。SQLiteの状態ファイルは、同じファイルの `open_state_database`（書き込み）・`read_state_database`（読み取りだけ）で開く。デバッグ専用hookのログは `<一時フォルダー>/ghc-agent-refinement/debug-logs/<ログ名>/` に、[debug_mode_state.py](../../../hooks/helpers/utilities/debug-mode-state/debug_mode_state.py) の `get_debug_log_directory` でセッションUUID別のフォルダーのパスを作って置き、`<一時フォルダー>/ghc-agent-refinement/` の直下には置かない。
   - パスを表示・保存・文字列比較・正規表現やglobで照合する場合は、`/` 区切りに揃えてから使う。`os.path.relpath` などOS依存の区切りを返すAPIの結果は、同じ式で `.replace(os.sep, '/')` を付けて変換する（`pathlib` では `.as_posix()`）。ファイルシステムのAPIに渡すだけのパスは変換しない。
   - 入力から受け取ったパス（ツール引数のファイルパスなど）は、`os.path.abspath` で絶対パスにしてからcwdやリポジトリルートと比較する。照合用の定義（除外パターンなど）は `/` 区切りで書く。
   - テストの期待値は `/` 区切りで書き、実行するOSによって期待値を変えない。
   - `.ps1` では、`Resolve-Path -Relative` などの結果に `-replace '\\', '/'` を付けて変換する。
8. 標準入力はバイト列として読み、UTF-8で復号する。入力の読み込みと `cwd`・`toolName`・`toolArgs`・`transcriptPath` の検証には [read_hook_input.py](../../../hooks/helpers/utilities/read-hook-input/read_hook_input.py) を使う。判定・助言のJSON（`permissionDecision`・`decision`・`additionalContext`）は [write_hook_output.py](../../../hooks/helpers/utilities/write-hook-output/write_hook_output.py) で標準出力に書く。hookの定義ファイル（JSON）の読み込みと値の検証・正規表現のコンパイルには [read_definitions.py](../../../hooks/helpers/utilities/read-definitions/read_definitions.py) を使う。標準出力・標準エラーはBOMなしのUTF-8で出力する。標準出力・標準エラーとテキストモードで書き込むファイルは、`newline='\n'` を指定して改行を `\n` に固定する（Windowsでは指定しないと `\n` が `\r\n` に変わり、出力や書き込んだ内容がOSによって変わる）。異常終了は原因を標準エラーに出力して `sys.exit(2)`、正常終了とスキップは `sys.exit(0)` または正常なスクリプト終端にする。Unicode escapeを使う場合は、復号した文字列を近くのコメントに記載する。
   - `except` 節で別の例外に変えて投げるときは `raise <新しい例外> from None` とし、元の例外から必要な情報（位置・理由など）は `{ex}` などで新しいメッセージに入れる。hookやテストのエラー表示は例外のメッセージ（`str(ex)`）だけで原因が分かるようにし、例外の連鎖（`During handling of the above exception...`）に頼らない。`from ex` と、`from` を付けない書き方は使わない。
   - 捕まえた例外を変えずに呼び出し元へ伝えるときは、素の `raise` と書く（`raise ex` とは書かない）。
9. デバッグ専用hookには `debug-` 接頭辞を付ける。状態は [set_debug_mode.py](../../../hooks/helpers/events/userPromptSubmitted/set-debug-mode/set_debug_mode.py) が書き込み、状態形式と保存先は [debug_mode_state.py](../../../hooks/helpers/utilities/debug-mode-state/debug_mode_state.py) で管理する。状態の読み取りには同じファイルの `get_debug_mode(session_id)` を使う。入力のセッションIDに対応する状態を読み、ファイルがない場合または `enabled` が `true` でない場合は正常終了する。
10. 個別hookのテストは、hookのフォルダーに `test_<hook_name>.py` として置く。通常実行、条件を満たさない場合のスキップ、不正入力時の動作と終了コードを検証する。拒否hookでは許可と拒否の両方を検証する。
    - hook定義の構文、起動コマンド、並び順、hookのフォルダーとの対応は [test_hook_configurations.py](../../../hooks/tests/test_hook_configurations.py) で一括して検証する。個別のテストでは、`matcher`・`timeoutSec` などhook固有の設定だけを、[hook_entry.py](../../../hooks/tests/helpers/hook_entry.py) を `import` で読み込み、`get_hook_entry(<実行スクリプトのパス>)` で取得した定義の要素で検証する。
    - hookが状態やログを書く場合は、テストごとの一時フォルダーを `TMPDIR`・`TEMP`・`TMP` に指定してhookを起動し、実際の一時フォルダーに書かせない。
    - テストから起動するプロセス（hook・`git` など）には、`subprocess.run(..., timeout=...)` や `communicate(timeout=...)` で上限を指定する。止まったプロセスで、テストや一括実行が止まらないようにする。
    - `unittest.TestCase` のテストメソッドとして記述する。[hook_test_output.py](../../../hooks/tests/helpers/hook_test_output.py) を `import` で読み込み、`__main__` 内で `run_tests()` を呼ぶ。import時にはテストを実行せず、一時領域は `setUp` と `addCleanup` などで管理する。
    - 一時領域に作るパスには、大文字小文字だけが違う名前（`App/` と `app/` など）を混ぜない。大文字小文字を区別しないファイルシステム（Windows・macOSの既定）では同じパスになり、OSによって結果が変わる。
    - 出力は `run_tests()` に任せ、独自の表示は加えない。複数入力の検証には `subTest`、スキップには理由付きの `skipTest` / `unittest.skip` を使う。
    - 共通出力処理を変更した場合は [test_hook_test_output.py](../../../hooks/tests/helpers/test_hook_test_output.py) も実行する。
11. Windows特有の制御はWindows PowerShell 5.1対応の `.ps1` で実装する。JSONの `powershell` にのみ `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .github/hooks/helpers/events/<event-name>/<hook-name>/<hook-name>.ps1` を記述する。補助ファイルは `$PSScriptRoot` を基準に `Join-Path` で階層ごとに連結する。標準入出力とファイル書き込みはBOMなしUTF-8を明示する。異常終了は原因を標準エラーに出力して `exit 2` にする。テストは `test-<hook-name>.ps1` に作成し、`powershell -NoProfile -File` で実行する。
12. hookの追加・削除、または発動するイベント・条件・役割を変更した場合は、`.github/README.md` の「発動フロー」にあるmermaidの図と表も更新する。表のhook列には、hook名を個別READMEへのリンクとして記載する。
    - ファイル全体は読まない。`grep -n` で対象イベントの図のノードと表の行を探し、その前後だけを範囲を指定して読んでから編集する。
    - mermaidの図に書くhookの内容は全角14文字以内にする（半角は全角の半分として数える）。表の内容列も図と同じ文言にする。
13. ユーザーがこのリポジトリ専用のhookとして作成するよう指示した場合は、配置先に `-repo-` を付ける。hook定義は `.github/hooks/-repo-<event-name>.json`、hookのファイルは `.github/hooks/helpers/-repo-events/<event-name>/<hook-name>/` に置き、JSONのコマンドではこの実行スクリプトをhookランナーに渡す。項目12の「発動フロー」は `.github/-repo-README.md` に記載する。skillの手順や他の項目にある `.github/hooks/<event-name>.json`・`helpers/events`・`.github/README.md` は、これらに読み替える。hook名には `-repo-` を付けない。指示がない場合は共通のhookとして作成する。
    - 名前が `-` で始まるファイル・フォルダーをコマンドの引数に単独で渡すと、オプションとして解釈される。`./-repo-preToolUse.json` のように `./` を付けるか、`--` の後に渡す。
14. Pythonスクリプトでは、モジュールの外から参照しない最上位の変数・関数・クラスの名前を `_` で始める。他のスクリプトが `import` して参照する名前、エントリーポイントの `main`、`unittest.TestCase` のサブクラスには付けない。自身のテストからだけ参照する名前には付け、テストでも `_` 付きの名前で参照する。モジュール直下の `for`・`with` で束縛する一時変数は対象外とする。
    - クラスの外から参照しないメソッド・クラス変数・インスタンス変数の名前は `__` で始め、名前マングリングを効かせる。基底クラスの属性を上書きするもの（`setUp` など）と、`unittest` が実行する `test` で始まるテストメソッドには付けない。`__` で始めた結果、同じクラスの既存の名前と重なる場合は、名前を変えて区別する。
15. Pythonスクリプトで、1行に書くと長くて要素の区切りが読み取りにくいタプルは、開き括弧の直後で改行し、要素を1行に1つずつ並べて、最後の要素にもカンマを付ける。閉じ括弧は、開き括弧のある行の字下げに揃えた行に置く。
    - 呼び出しの唯一の引数がタプルの場合は、`events.append((` のように括弧を重ねて開き、`))` で閉じる。外側の括弧の直後では改行しない。
    - 同じ形の行が並ぶ表（テストの入力の一覧など）や、同じ形の文が続く箇所は、揃いを優先して1行のままにしてよい。
