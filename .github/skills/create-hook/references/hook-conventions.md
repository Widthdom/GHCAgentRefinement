# hookの共通規約

1. Copilot SDKのイベント、設定、入出力仕様を使い、SDK以外の形式向けの互換処理、フォールバック、専用の実装・検証は加えない。Windows特有の制御を行うhookを除き、Windows、macOS、Linuxのすべてを対象にする。イベントは対象VS Codeバージョンで使えることを確認する。
2. hook定義はイベントごとに `.github/hooks/<event-name>.json` の配列にまとめ、要素を実行スクリプトパスの昇順に並べる。
3. hookのファイルは `.github/hooks/helpers/events/<event-name>/<hook-name>/` に置き、実行スクリプトは `<hook-name>.py` とする。複数hookで共有する補助スクリプトは [.github/hooks/helpers/utilities/](../../../hooks/helpers/utilities/) に置く。
4. JSONのコマンドは、[hookランナー](../../../hooks/helpers/runner/README.md) に実行スクリプトのリポジトリルート相対パスを渡して起動する。`bash` には `python3 .github/hooks/helpers/runner/run-hook.py <実行スクリプト>`、`powershell` には `python .github/hooks/helpers/runner/run-hook.py <実行スクリプト>` を記述する。Pythonが無い環境でも動く必要があるhook（`check-python`、`block-commands`）だけは、ランナーを通さず `.sh`・`.cmd` から起動する。
5. 各hookのフォルダーに `README.md` を作成し、発動条件と処理詳細を記載する。設計背景は必要な場合だけ記載する。
6. Python 3.14対応の構文・標準ライブラリAPIを使う。最低対応・推奨は3.14とし、新規インストールには3.14系の最新パッチ版を案内する。
7. 補助ファイルは `os.path.dirname(os.path.abspath(__file__))` を基準に参照する。パスは `os.path.join` で階層ごとに連結し、親は `os.path.dirname`、一時ファイルの基点は `tempfile.gettempdir()` を使う。hookが実行をまたいで保持する状態は `<一時フォルダー>/ghc-agent-refinement/hook-states/<状態名>/`、デバッグ専用hookのログは `<一時フォルダー>/ghc-agent-refinement/debug-logs/<ログ名>/` に置き、`<一時フォルダー>/ghc-agent-refinement/` の直下には置かない。
8. 標準入力はバイト列として読み、UTF-8で復号する。標準出力・標準エラーはBOMなしのUTF-8で出力する。異常終了は原因を標準エラーに出力して `sys.exit(2)`、正常終了とスキップは `sys.exit(0)` または正常なスクリプト終端にする。Unicode escapeを使う場合は、復号した文字列を近くのコメントに記載する。
9. デバッグ専用hookには `debug-` 接頭辞を付ける。状態形式と保存先は [set-debug-mode.py](../../../hooks/helpers/events/userPromptSubmitted/set-debug-mode/set-debug-mode.py) 、状態の読み取りには [get-debug-mode.py](../../../hooks/helpers/utilities/get-debug-mode.py) の `get_debug_mode(session_id)` を使う。入力のセッションIDに対応する状態を読み、ファイルがない場合または `enabled` が `true` でない場合は正常終了する。
10. 個別hookのテストは、hookのフォルダーに `test-<hook-name>.py` として置く。JSON構文、通常実行、条件を満たさない場合のスキップ、不正入力時の動作と終了コードを検証する。拒否hookでは許可と拒否の両方を検証する。
    - `unittest.TestCase` のテストメソッドとして記述する。[.github/hooks/tests/helpers](../../../hooks/tests/helpers) を `sys.path` に追加して [hook_test_output.py](../../../hooks/tests/helpers/hook_test_output.py) を読み込み、`__main__` 内で `run_tests()` を呼ぶ。import時にはテストを実行せず、一時領域は `setUp` と `addCleanup` などで管理する。
    - 出力は `run_tests()` に任せ、独自の表示は加えない。複数入力の検証には `subTest`、スキップには理由付きの `skipTest` / `unittest.skip` を使う。
    - 共通出力処理を変更した場合は [test-hook-test-output.py](../../../hooks/tests/helpers/test-hook-test-output.py) も実行する。
11. Windows特有の制御はWindows PowerShell 5.1対応の `.ps1` で実装する。JSONの `powershell` にのみ `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .github/hooks/helpers/events/<event-name>/<hook-name>/<hook-name>.ps1` を記述する。補助ファイルは `$PSScriptRoot` を基準に `Join-Path` で階層ごとに連結する。標準入出力とファイル書き込みはBOMなしUTF-8を明示する。異常終了は原因を標準エラーに出力して `exit 2` にする。テストは `test-<hook-name>.ps1` に作成し、`powershell -NoProfile -File` で実行する。
12. hookの追加・削除、または発動するイベント・条件・役割を変更した場合は、[.github/README.md](../../../README.md) の「発動フロー」にあるmermaidの図と表も更新する。表のhook列には、hook名を個別READMEへのリンクとして記載する。
13. ユーザーがこのリポジトリ専用のhookとして作成するよう指示した場合は、配置先に `-repo-` を付ける。hook定義は `.github/hooks/-repo-<event-name>.json`、hookのファイルは `.github/hooks/helpers/-repo-events/<event-name>/<hook-name>/` に置き、JSONのコマンドではこの実行スクリプトをhookランナーに渡す。項目12の「発動フロー」は `.github/-repo-README.md` に記載する。skillの手順や他の項目にある `.github/hooks/<event-name>.json`・`helpers/events`・`.github/README.md` は、これらに読み替える。hook名には `-repo-` を付けない。指示がない場合は共通のhookとして作成する。
    - 名前が `-` で始まるファイル・フォルダーをコマンドの引数に単独で渡すと、オプションとして解釈される。`./-repo-preToolUse.json` のように `./` を付けるか、`--` の後に渡す。
