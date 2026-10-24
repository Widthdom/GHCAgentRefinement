# hookの共通規約

1. Copilot SDKのイベント、設定、入出力仕様を使い、SDK以外の形式向けの互換処理、フォールバック、専用の実装・検証は加えない。Windows、macOS、Linuxのすべてを対象にし（Windows特有の制御を行うhookを除く）、対象VS Codeバージョンでイベントを確認する。
2. hook定義はイベントごとに `.github/hooks/<event-name>.json` にまとめ、同じイベントの複数hookを配列に記述する（配列要素順は実行スクリプトパスの昇順）。実行・補助スクリプトは `.github/hooks/helpers/events/<event-name>/<hook-name>/` に配置する。複数hookで共有する補助スクリプトは `.github/hooks/helpers/utilities/` に配置する。ファイル名にはhook名を使い、実行スクリプトはPython（`.py`）で作成する。JSONの `bash` / `powershell` コマンドには実行スクリプトのリポジトリルート相対パスを指定する。
3. 処理自体が複雑で説明が必要な場合や、設計背景を補足する必要がある場合は `.github/hooks/helpers/events/<event-name>/<hook-name>/README.md` に動作や背景を記載する。
4. macOS/Linuxは `bash` に `python3`、Windowsは `powershell` に `python` での起動を記述する。同じ `.py` を使い、OS固有のシェル構文に依存させない。
5. Python 3.14対応の構文・標準ライブラリAPIを使う。最低対応・推奨は3.14とし、新規インストールには同系列の最新パッチ版を案内する。基準の明示的な更新までは推奨系列を変えない。
6. 補助ファイルは `os.path.dirname(os.path.abspath(__file__))` を基準に参照する。パスは `os.path.join` で階層ごとに連結し、親は `os.path.dirname`、一時ファイルの基点は `tempfile.gettempdir()` を使う。
7. 標準入力はバイト列として読み、UTF-8で復号する。標準出力・標準エラーはBOMなしのUTF-8で出力する。異常終了は原因を標準エラーに出力して `sys.exit(2)`、正常終了とスキップは `sys.exit(0)` または正常なスクリプト終端にする。Unicode escapeを使う場合は、復号した文字列を近くのコメントに記載する。
8. デバッグ専用hookには `debug-` 接頭辞を付ける。状態形式と保存先は `.github/hooks/helpers/events/userPromptSubmitted/set-debug-mode/set-debug-mode.py`、状態の読み取りには `.github/hooks/helpers/utilities/get-debug-mode.py` の `get_debug_mode(session_id)` を使う。入力のセッションIDに対応する状態を読み、ファイルがない場合または `enabled` が `true` でない場合は正常終了する。
9. 個別hookのテストは `.github/hooks/helpers/events/<event-name>/<hook-name>/test-<hook-name>.py` に配置する。macOS/Linuxでは `python3`、Windowsでは `python` を使い、Python 3.14でテストする。JSON構文、通常実行、条件を満たさない場合のスキップ、不正入力時の動作と終了コードを検証する。拒否hookでは許可と拒否の両方を検証する。
   - テストは `unittest.TestCase` のテストメソッドとして記述し、[hook_test_output.py](../../../hooks/tests/hook_test_output.py) の `run_tests()` で実行する。`__main__` 内で呼び出し、`.github/hooks/tests` を `sys.path` に追加してから読み込む。import時にはテストを実行せず、一時領域は `setUp` と `addCleanup` などで管理する。
   - 出力は `unittest` 標準のドット、`Ran <件数> tests in <秒数>s`、`OK` を使う。失敗・例外・予期しない成功では詳細と `FAILED (...)` に続けて `NG` を出力し、終了コード1を返す。表示は標準エラーに出力し、独自の成功件数表示は加えない。
   - 件数は `unittest` のテストメソッド数（スキップを含む）とし、複数入力の検証には `subTest` を使う（個別に件数へ加算しない）。スキップは `skipTest` / `unittest.skip` で理由を記録し、詳細は `-v` で確認できる。共通出力処理を変更した場合は [test-hook-test-output.py](../../../hooks/tests/test-hook-test-output.py) も実行する。
10. Windows特有の制御はWindows PowerShell 5.1対応の `.ps1` で実装する。JSONの `powershell` にのみ `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .github/hooks/helpers/events/<event-name>/<hook-name>/<hook-name>.ps1` を記述する。補助ファイルは `$PSScriptRoot` を基準に `Join-Path` で階層ごとに連結する。標準入出力とファイル書き込みはBOMなしUTF-8を明示する。異常終了は原因を標準エラーに出力して `exit 2` にする。テストは `test-<hook-name>.ps1` に作成し、`powershell -NoProfile -File` で実行する。
