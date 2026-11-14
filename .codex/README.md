# .codex README

Codexで共通hookを実行するための設定を置く。共通指示は [AGENTS.md](../AGENTS.md)、skillは [.agents/skills](../.agents/skills) から読み込む。

## 前提条件

- ローカルのCodexで、プロジェクトの `.codex/hooks.json`、`commandWindows`、下表のイベントをサポートする版を使う。調査環境のCLIは `0.169.0`。公式ガイドは2026-11-15に確認した。古い版やVS Codeの別のSession Targetで同じ動作をするとは限らない。
- GitとPython 3.14以降をPATHから実行できること。macOS/Linuxは `python3`、Windowsは `python` とWindows PowerShellを使う。Pythonを新規導入する場合は3.14系の最新パッチ版を使う。
- プロジェクトとhookをCodexで信頼済みにする。CLIの `/hooks` で内容を確認して信頼する。新規・変更された未承認hookはスキップされる。ユーザーの信頼設定をリポジトリから書き換えることはしない。
- hook機能は公式の既定では有効。ユーザーや管理者の設定で無効になっている場合は実行されない。

## 構成

|ファイル|役割|
|--|--|
|[hooks.json](./hooks.json)|イベントごとにアダプターを1つ登録する。|
|[run-codex-hook.py](../.github/hooks/helpers/runner/run-codex-hook.py)|Codexの入力と履歴をCopilot SDK形式に変換する。|
|[adapted-hook-runtime.py](../.github/hooks/helpers/runner/adapted-hook-runtime.py)|Claude Codeと共通の定義読込・順次実行・出力集約。|
|[test-run-codex-hook.py](../.github/hooks/helpers/runner/test-run-codex-hook.py)|形式変換、拒否、停止、起動設定のテスト。|

Codexは同一イベントのコマンドhookを並列実行するため、アダプターを1つ登録し、その中で共通hookを定義順に実行する。hook一覧は `.github/hooks` の定義だけに保持する。

起動パスは `git rev-parse --show-toplevel` で解決するため、サブディレクトリからCodexを開始しても使える。Windowsは `commandWindows` でPowerShellからPythonを起動する。Claude Code用のGit Bash起動スクリプトには依存しない。

## イベントの対応

|Codex|Copilot SDK|処理|
|--|--|--|
|`SessionStart`|`sessionStart`|起動元の `source` を渡す。|
|`UserPromptSubmit`|`userPromptSubmitted`|ユーザーの `prompt` を渡す。|
|`SubagentStart`|`userPromptSubmitted`|`agentId` を渡す。依頼文は入力にないため空文字列。|
|`PreToolUse`|`preToolUse`|ツール名と引数を変換して拒否を集約する。|
|`PostToolUse`|`postToolUse`|非ゼロ終了のシェルも含む。出力の文字列を `toolResult.textResultForLlm` に渡す。|
|`Stop`|`agentStop`|親の履歴と最終応答を検査する。|

`SubagentStop` は対象外とし、登録・変換しない。共通の `agentStop` hookはサブエージェントの停止を対象外としているため、停止時の検査は親の `Stop` だけで実行する。

`PostToolUseFailure` と `userPromptTransformed` に対応するイベントは登録しない。このため、失敗の繰り返し検出と変換後プロンプトの記録はCodexでは働かない。承認を代行する `PermissionRequest` も登録しない。

## 入力と実行

- `session_id`、`cwd`、現在時刻を `sessionId`、`cwd`、`timestamp` に変換する。UUIDでないセッションIDは名前ベースUUIDに変換する。
- Codexのサブエージェントhookは親のセッションIDを持つため、`agent_id` があれば親と子のIDの組から名前ベースUUIDを作り、状態の衝突を防ぐ。`agentId` も渡す。
- Codexの正規化済みツール名 `Bash` は `bash` に変換する。履歴中の `exec_command`、`shell_command`、`shell` も同じ形式にし、`cmd` やコマンド配列を `command` に変換する。
- `apply_patch` はそのまま渡す。パッチ本文は `tool_input.command` に入るため、既存の編集用utilityが読める。履歴中の自由形式パッチは `command` に包む。
- MCPなどのその他のツール名・引数は保持する。共通hookに対応する判定がなければ処理対象にならない。
- `.github/hooks/-repo-<event>.json`、`.github/hooks/<event>.json` の順に読み、変換後のツール名に `matcher` が一致するhookを順次実行する。拒否があっても残りのhookは実行する。
- ランナーを通すPython hookは同一プロセスで実行する。直接起動のhookはOSに応じて定義の `bash` または `powershell` を使う。詳細は [runner README](../.github/hooks/helpers/runner/README.md) を参照。

## 停止時の履歴

`transcript_path` は公式にも非安定インターフェースとされている。アダプターは公開ソースのrollout JSONL（`type`、`payload`）を対象に、次の情報を一時的なCopilot形式の履歴に変換する。

- 旧形式の `event_msg.user_message` / `agent_message`、ページ分割形式の `item_completed` の `UserMessage` / `AgentMessage`。
- `response_item` の `function_call` / `custom_tool_call` とその出力。編集ツールの引数も保持する。
- `CommandExecution.exit_code`、構造化されたシェル出力の `metadata.exit_code` / `exit_code`、シェル出力の終了行から確定できる終了コード。起動中・拒否・終了コード不明のシェルを成功扱いしない。
- ページ分割形式の `HookPrompt` を差し戻し履歴として渡す。共通hookの差し戻し文は個別に復元する。
- 入力の `last_assistant_message` が末尾の応答と異なる場合は補う。自身の `agentStop` 開始も末尾に追加し、共通hookの履歴待機を終わらせる。

指示や圧縮要約を、ユーザーが入力した依頼として補完しない。一時的な履歴は実行終了時に削除する。読めない・空・未知の履歴は標準エラーに報告し、その回の停止検査を実行しない。

## 出力

- ツールの拒否は `hookSpecificOutput.permissionDecision: deny` と理由にまとめる。
- 停止時の差し戻しは `decision: block` と `reason` にまとめる。Codexが続行したあとの `stop_hook_active: true` では再ブロックしない。差し戻しは1ターンにつき最大1回となる。
- 停止時以外の `additionalContext` はイベント名付きの `hookSpecificOutput` にまとめる。`systemMessage` はトップレベルにまとめる。
- 停止時の単なる助言は続行理由に変えない。
- アダプターやhookの異常は終了コード1で報告する。Codexの終了コード2はブロックを意味するため、内部エラーには使わない。他のhookから判定が得られた場合は終了コード0で判定を返す。

## 制約と限界

- 共通hookをPythonなしで起動できない。Python未導入時には `check-python`、`block-commands` も動かない。
- Codexがhookに渡さない呼び出し（ホスト側Web検索など）は検査できない。`write_stdin` は既存プロセスの通信であり、再度の実行前検査はない。コードモード内部のツールが履歴に独立した呼び出しとして残らない場合、停止検査でも復元できない。
- 共通hookの検出対象がCopilotのツールに限られる場合、別名のMCPツールなどには同じ検出が働くとは限らない。
- 古い履歴で後続の `write_stdin` にだけ終了コードがある場合、元のコマンドの終了コードを推定しない。ページ分割形式の `CommandExecution` があれば元の呼び出しIDで補う。
- 履歴のロールバックや圧縮による置換・欠落、旧形式の割り込みの依頼区切りは完全には再現しない。履歴依存の停止検査は取得できた範囲に限る。
- Python hook個別の `timeoutSec` は同一プロセス内では適用されない。アダプター全体にCodexのタイムアウト（設定省略時600秒）が適用される。直接起動するhookには定義の `timeoutSec` を適用する。
- テストはアダプターを子プロセスで実行する方式。Codexによる信頼済みhookの実発火とWindows/Linux上の実行は、この変更時には確認していない。

## 変更するとき

1. 公式ガイドでイベント・入出力・起動方法を確認し、履歴を扱う場合は公開ソースの保存形式も確認する。
2. Codex固有の変換はアダプターに加える。共通hookにはCodex専用の分岐を加えない。
3. イベントを増減した場合は `hooks.json` と上表を合わせる。
4. `python3 .github/hooks/helpers/runner/test-run-codex-hook.py`、`python3 .github/hooks/tests/test-all-hooks.py`、`python3 .github/hooks/tests/test-hook-configurations.py` を実行する。Windowsでは `python` を使う。

## 確認した公式情報

- [Hooksガイド](https://learn.chatgpt.com/docs/hooks)：イベント、設定探索、信頼、OS別コマンド、入出力、履歴の非安定性。
- [hook設定型](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/protocol/src/hooks.rs) と [command runner](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/hooks/src/engine/command_runner.rs)：`commandWindows` とWindowsでのCOMSPEC経由の実行。
- [rolloutのwire形式](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/history/src/rollout_payload.rs)、[保存対象の規則](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/rollout/src/policy.rs)、[TurnItem型](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/protocol/src/items.rs)：旧形式・ページ分割形式の変換根拠。

公開ソースは調査時のmainをコミットで固定した参照であり、すべてのリリース版で同じ形式になる保証ではない。
