# block-commands hook README

## 発動条件
preToolUse（bashまたはpowershellツールの実行前）

## 処理詳細
1. Windowsでは `python`、macOS/Linuxでは `python3` が見つからない場合、またはPython 3.14未満の場合、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。
2. 1.以外の場合、標準入力を引き継いで[py](./block-commands.py)を実行する。
3. [blocked-command-definitions.json](./blocked-command-definitions.json) が存在しない場合、フォーマット違反の場合、不正な正規表現がひとつでも含まれていた場合は異常終了し、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。
4. 呼び出すツール（`toolName`・`toolArgs`）がbash・powershellで、そのコマンドが `blocked-command-definitions.json` に定義された正規表現に一致した場合は、定義の `reason` を拒否理由とするツール実行拒否JSONを標準出力し、正常終了する。標準エラーには出力しない。

## 備考
- bashとpowershell以外のツール呼び出しは処理対象外。
- Python未インストール時であっても機能するように、Windowsでは[cmd](./block-commands.cmd)、macOS/Linuxでは[sh](./block-commands.sh)を呼び出す。
- Windows用バッチはコメントも含めASCIIで記述する。UTF-8の日本語コメントは、cmd.exeのコードページによってコメントの一部がコマンドとして解釈されるため、使用しない。

### check-python-version.pyの呼び出しを `call python "%%check_python_version_path%%"` とする理由
- Windowsでは、`python.exe` でなく `python.cmd` が使用される場合がある。
  - 後者の場合、call無しで実行すると呼び出し元に戻らないため、callで実行する必要がある。
- `call` は構文解析時（1度目）と実行時（2度目）の計2回、パーセント記号（%）の変数展開を行う仕様になっている。
  - %%check_python_version_path%% のようにすることで、仮にcheck_python_version_pathの中に文字通り `%USERPROFILE%` のようなフォルダ名が含まれていても、 `call` の2度の展開に耐える、pathの崩れを回避可能。

### block-commands.pyの呼び出しを `python "%~dp0block-commands.py" %*` とする理由
- `call` を使うと、`%*`で渡した引数が再解析され、引数中の `%USERPROFILE%` などが展開される可能性がある。
- `call` を使わない以上、`python.cmd` の場合は戻ってこないが、後続の処理は終了コードを返すのみなので、対応は見送りとする。
