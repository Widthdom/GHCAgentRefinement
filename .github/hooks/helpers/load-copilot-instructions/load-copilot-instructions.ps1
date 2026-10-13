# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

$instructionsPath = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) 'copilot-instructions.md'
# -LiteralPath を指定することで引数をワイルドカードとして解釈せず、文字どおりのパスとして扱う。
# -PathType Leaf を指定することで、ファイルとして存在するかどうかを確認する。
if (-not (Test-Path -LiteralPath $instructionsPath -PathType Leaf)) {
    [System.Console]::Error.WriteLine("Copilot instructions file not found: $instructionsPath")
    exit 2
}

# copilot-instructions.md には日本語が含まれている可能性があるため、UTF-8で読み込む。
$instructions = [System.IO.File]::ReadAllText($instructionsPath, [System.Text.Encoding]::UTF8)
if ([string]::IsNullOrWhiteSpace($instructions)) {
    [System.Console]::Error.WriteLine("Copilot instructions file is empty: $instructionsPath")
    exit 2
}

# 標準出力をUTF-8に統一することで、先頭にBOMが含まれJSON解析を妨げることのないようにする。
#   右辺はSystem.Text.UTF8Encodingクラスのインスタンス化。コンストラクタの引数にfalseを渡すとエンコーダによるBOMの出力を無効にする。
[System.Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# -Compress はJSONの余分な空白や改行を省き、1行のJSONにする指定
$output = @{ additionalContext = $instructions } | ConvertTo-Json -Compress

# パイプライン出力（式の評価結果をそのまま流す形式）でも Copilot に返却可能だが、
# [System.Console]::Out.WriteLine で明示的に出力することで、
# 不要な出力の混入やフォーマット乱れを防ぎ、純粋な JSON 文字列を確実に渡すことができる。
[System.Console]::Out.WriteLine($output)
