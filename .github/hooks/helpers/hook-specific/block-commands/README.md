# block-commands hook README

## 発動条件
preToolUse（bashまたはpowershellツールの実行前）

## 処理詳細
1. Windowsでは `python`、macOS/Linuxでは `python3` が見つからない場合、またはPython 3.14未満の場合、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。
2. 1.以外の場合、標準入力を引き継いで[py](./block-commands.py)を実行する。
3. `blocked-command-definitions.json` が存在しない場合、フォーマット違反の場合、不正な正規表現がひとつでも含まれていた場合は異常終了し、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。
4. ツール呼び出しのコマンドが、`blocked-command-definitions.json` に定義された正規表現に一致した場合は正常終了し、標準エラーにその旨を出力したうえでツール実行拒否JSONを標準出力する。

## 備考
- bashとpowershell以外のツール呼び出しは処理対象外。
- Python未インストール時であっても機能するように、Windowsでは[cmd](./block-commands.cmd)、macOS/Linuxでは[sh](./block-commands.sh)を呼び出す。
