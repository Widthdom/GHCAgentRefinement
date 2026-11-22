# create-hook skill README

## 作成と更新の分担
[create-hook](./SKILL.md) は新規作成、[update-hook](../update-hook/SKILL.md) は既存hookの更新・削除を担当する。スクリプトの移動時の参照更新や、変更の影響範囲を踏まえた回帰検証は更新側にまとめる。両方が従う実装・検証規約は [共通規約](./references/hook-conventions.md) にまとめ、二重管理しない。

## 対象ランタイム
VS CodeのSession Targetが `Copilot` の場合はCLIと同じSDK hook実装を使うため、SDKの仕様に従えば両方で動く。一方、`Local`、`Claude`、`Codex` は異なる実行環境であり、同じ設定が読み込まれても互換性があるとは限らない。そのため、hookにはSDK以外の形式向けの互換処理を加えない。CodexとClaude Codeには、hookの外側に置いたアダプターで対応する（[Codex](../../../.codex/README.md)、[Claude Code](../../../.claude/README.md)）。hook自体はSDKの形式だけを扱えばよい。各アダプターの対応イベント・ツール・履歴の制約を確認する。VS Codeのバージョンによってイベントの対応状況が異なるため、対象バージョンでイベントを確認する。

## 実装言語
hookの実行スクリプトをPythonに統一すると、Windows、macOS、Linuxで同じスクリプトを起動でき、OSごとにシェルスクリプトを書き分ける必要がなくなる。JSON・正規表現・パス操作・一時ディレクトリなど、hookで必要な処理は標準ライブラリだけで扱える。

## Pythonなしで動くhookの起動
`check_python` と `block_commands` は、Pythonがない環境でも案内や拒否ができる必要があるため、hookランナーを通さず、hook定義の `bash` からは `.sh` を、`powershell` からは `.cmd` を起動する。

### Copilot・Claude Code・Codexでの起動のされ方
- Copilotは、Windowsではhook定義の `powershell` のコマンドをPowerShellで実行し、macOS・Linuxでは `bash` のコマンドを実行する。
- Claude Code・Codexでは、アダプターがこの2つのhookを、Windowsでは定義の `powershell` のコマンドのまま、それ以外では `bash` のコマンドのまま子プロセスで実行する。アダプター自体はPythonで動くため、Pythonがない環境ではこの2つのhookも働かない（[hookランナーのREADME](../../hooks/helpers/runner/README.md#共通の実行)）。Claude Codeでは、Windowsでも起動スクリプトをGit Bashで実行する（[.claude/README.md](../../../.claude/README.md)）。

### Gitを前提としても `.cmd` を `.sh` に統一しない理由
Gitのインストールを前提とし、WindowsではGit for Windows（Git Bashを含む）があるとみなしている。それでも、`powershell` のコマンドを `bash .../xxx.sh` にして `.cmd` を廃止することはしない。

- Copilotは、Windowsでは `powershell` のコマンドをPowerShellで実行するため、`bash` はPATHから探される。
- Git for Windowsは、既定のインストールでは `Git\cmd`（`git.exe` など）だけをPATHに加え、`bash.exe` のある `Git\bin` は加えない。
- 代わりに、WSLの起動用の `C:\Windows\System32\bash.exe` や `%LOCALAPPDATA%\Microsoft\WindowsApps\bash.exe` が見つかる（Windows 11の実機で、PowerShellの `Get-Command bash` がこの順に返すことを確認した）。この場合、スクリプトはWSLの中で動き、PythonもWSL側のものを使う。WSLのディストリビューションがなければエラーになる。
- Git Bashのフルパスを書く方法もあるが、インストール先は環境によって異なる。
- Claude Codeは自分でGit Bashを探すため、この問題は起きない。ただし、Windowsではアダプターが `.cmd` を実行するため、`.cmd` をなくしてもClaude Code側の構成は簡単にならない。
- 対象はこの2つのhookだけで、`.cmd` も短いため、残しても保守の負担は小さい。

## Windows特有の制御
サービスの制御やレジストリ、CIM/WMIなどのWindows管理機能は、Windows PowerShellのコマンドレット（`Get-Service`、`Restart-Service` など）から直接扱える。Pythonで同じことをするには外部コマンドの呼び出しや追加パッケージが必要になる。Windows PowerShell 5.1はWindowsに標準搭載されており追加インストールが不要なため、Windowsでしか意味を持たない処理は5.1で実装する。

5.1ではPowerShell 7以降の構文や機能（`??`、`?.`、三項演算子、`$IsWindows`、`ConvertFrom-Json -AsHashtable` など）を使えない。また、`Set-Content -Encoding UTF8` などはBOM付きで書き込むため、BOMなしUTF-8は `[System.Text.UTF8Encoding]::new($false)` で明示する。

## スクリプト配置とパス
スクリプト、README、テストをhook名ごとのフォルダーにまとめることで、関連ファイルの対応関係を保ちやすくなる。hookから起動するスクリプトは呼び出し元の作業ディレクトリを前提にできないため、補助ファイルはスクリプト自身の場所（`__file__`）を基準に参照する。`os.path.join` と標準パスAPIを使うことで、OS間のパス区切りや親ディレクトリ表記の違いに依存しなくなる。

`script_directory + '\\x.py'` のような区切りの直書きや、`os.path.join(base, 'a/b')` のような複数階層を含む引数は避け、`os.path.join(base, 'a', 'b')` とする。親ディレクトリには `'..'` ではなく `os.path.dirname` を使う。

`os.path.join` などの標準パスAPIはOSに合わせた区切りでパスを返すため、ファイルを開くだけなら問題ないが、その文字列を表示・保存・比較すると、Windowsでは `\`、macOS・Linuxでは `/` の区切りになる。たとえば `os.path.relpath` の結果を `/` 区切りの除外パターンで照合すると、Windowsでだけ一致しない。逆に、Windowsで作ったテストの期待値に `\` を書くと、macOS・Linuxで失敗する。どちらも開発したOSではテストが通るため、気付きにくい。表示・保存・照合に使うパスは `/` 区切りに揃え、定義とテストの期待値も `/` 区切りで書くことで、OSによらず同じ結果にする。

## 名前の公開範囲
hookやutilityのスクリプトは他のスクリプトから `import` されるため、モジュールの最上位にある名前（`import` で付けた名前を含む）はすべて他のスクリプトから参照できる。外から参照しない名前を `_` で始めると、他のスクリプトが依存してよい名前とそうでない名前が区別でき、内部の名前を変更するときに影響範囲を調べる必要がなくなる。

クラスの内部だけで使うメンバーは `__` で始めると、Pythonが `_クラス名__名前` に変換（名前マングリング）するため、クラスの外からの参照や、サブクラスで同じ名前を定義したときの衝突を防げる。

## 入出力のエンコーディング
Pythonの標準入出力の既定エンコーディングはOSやロケールに依存し、Windowsでは日本語などを含むJSONを正しく読み書きできない場合がある。標準入力をバイト列で読んでUTF-8で復号し、出力もBOMなしのUTF-8に揃えることで、OSによらず同じJSONを扱える。

## 終了コード
Copilot SDK/CLIの `preToolUse` command hookでは非ゼロ終了が拒否扱いになる一方、VS Code Localでは `exit 1` が非ブロッキング警告、`exit 2` がブロッキングエラーを表す。Localは対象外だが、異常終了を `exit 2` に統一すると失敗の意味が明確になり、hook間の扱いも揃う。CodexとClaude Codeでも `exit 2` はブロックを表すため、アダプターが非ブロックの `exit 1` に変えて返す。明示的な判定がある場合は終了コード0とJSONで伝える。

## 発動フローの図の文言
VS Codeのプレビューでは、mermaidの図形内の1行が先頭の `・` を含めて全角15文字を超えると、図形内で改行されて読みにくくなる。そのため、図に書くhookの内容は `・` を除いて全角14文字以内とする。半角の文字は全角の半分程度の幅になるため、半分として数える。

## デバッグモードの状態
セッションIDごとにデバッグ状態を読むことで、別セッションの設定を引き継がず、明示的に有効化されたセッションだけでデバッグ専用hookを実行できる。状態がない場合や無効の場合にスキップすれば、通常利用への影響を避けられる。
