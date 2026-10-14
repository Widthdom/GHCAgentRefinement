# block-commands hook README

## 発動条件
preToolUse（bashまたはpowershellツールの実行前）

## 処理詳細
1. Windowsでは[cmd](./block-commands.cmd)、Linux/macOSでは[sh](./block-commands.sh)を起動する。
2. `pwsh` が見つからない場合は、標準エラーに理由を出力し、`permissionDecision: "deny"` と終了コード `2` を返してツール実行を拒否する。
3. `pwsh` が見つかった場合は、標準入力を引き継いで[検査スクリプト](./block-commands.ps1)を実行する。
4. ツール呼び出しのコマンドを読み取り、`blocked-command-patterns.json` に定義された正規表現と照合する。
5. 一致した場合は実行を拒否し、定義に記載された理由を返す。一致しない場合は拒否しない。

## 備考
- bashとpowershell以外のツール呼び出しは処理対象外。
- 導入や更新は自動実行しない。
- ラッパーは検査スクリプトの標準出力・標準エラー・終了コードを引き継ぐ。
- [テスト](./test-block-commands.ps1)は `pwsh -NoProfile -File .github/hooks/helpers/block-commands/test-block-commands.ps1` で実行する。Windowsではcmd、bashが利用可能な環境ではshについても、通常時の判定と `pwsh` 不在時の拒否を検証する。
