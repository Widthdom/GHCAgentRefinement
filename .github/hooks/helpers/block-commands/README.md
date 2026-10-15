# block-commands hook README

## 発動条件
preToolUse（bashまたはpowershellツールの実行前）

## 処理詳細
1. Windowsでは[cmd](./block-commands.cmd)、macOS/Linuxでは[sh](./block-commands.sh)を起動する。
2. Windowsでは `python`、macOS/Linuxでは `python3` が見つからない場合、またはPython 3.14未満の場合、ツール実行を拒否する。
3. 2.以外の場合、標準入力を引き継いで[py](./block-commands.py)を実行する。
4. ツール呼び出しのコマンドが、`blocked-command-patterns.json` に定義された正規表現に一致した場合はツール実行を拒否する。

## 備考
- bashとpowershell以外のツール呼び出しは処理対象外。
