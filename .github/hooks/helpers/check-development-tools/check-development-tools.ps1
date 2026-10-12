param(
    # ValidateSet は直後の引数に設定可能な値に制限を加える。
    [ValidateSet('Auto', 'Windows', 'MacOS', 'Linux')] [string]$Platform = 'Auto',
    [string]$WingetCommand = 'winget',
    [string]$BrewCommand = 'brew',
    [string]$AptCommand = 'apt-cache',
    [string]$DnfCommand = 'dnf',
    [string]$PacmanCommand = 'pacman'
)

# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

# 標準入力をUTF-8として復号化し、JSON内の日本語などを正しく読み取る。
#   左辺はSystem.Consoleクラスに属する静的メンバInputEncodingにアクセスしていることを意味する。
[System.Console]::InputEncoding = [System.Text.UTF8Encoding]::new()

# 標準出力をUTF-8に統一することで、先頭にBOMが含まれJSON解析を妨げることのないようにする。
#   右辺はSystem.Text.UTF8Encodingクラスのインスタンス化。コンストラクタの引数にfalseを渡すとエンコーダによるBOMの出力を無効にする。
[System.Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$helperRoot = $PSScriptRoot
# 冒頭のドットは、指定したスクリプトを現在のスコープで実行することを意味する。
#   ドットなしで実行すると、スクリプトは新しいスコープで実行され、その中で定義された関数や変数は終了時に破棄される。
. (Join-Path $helperRoot 'common.ps1')
# @() は配列を表す。
$checkers = @('powershell', 'git', 'ripgrep', 'python')
$issues = foreach ($checker in $checkers) {
    $scriptPath = Join-Path $helperRoot "check-$checker.ps1"
    # & は呼び出し演算子で、変数や文字列に入っているパスやコマンド名を、コマンドとして実行する。
    # 冒頭にドットがないので、スクリプトは新しいスコープで実行される。
    # 行末のバッククォート ` は、行を継続して次の行へ続ける記号である。
    @(& $scriptPath `
        -Platform $Platform `
        -WingetCommand $WingetCommand `
        -BrewCommand $BrewCommand `
        -AptCommand $AptCommand `
        -DnfCommand $DnfCommand `
        -PacmanCommand $PacmanCommand)
}

# Where-Object を使ってフィルターをかける。空白やnullでない要素のみを残す。
#   $_ は各要素を表す。
$issues = @($issues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($issues.Count -eq 0) {
    exit 0
}

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform
# Get-DevelopmentPlatformは 公式の名称である'macOS'でなく、'MacOS'を返すため、表示用に補正する。
$platformDisplayName = if ($resolvedPlatform -eq 'MacOS') {
    'macOS'
} else {
    $resolvedPlatform
}


# @" と "@ は、ヒアストリング(here-string)の開始と終了を表す。
#   特徴
#     ・改行や引用符をそのまま含められる。たとえばダブルクオーテーションをエスケープする必要がない。
#     ・変数展開はされる。たとえばここでの $platformDisplayName はその実値に置き換えられる。
#     ・式展開はされる。たとえばここでの $($issues -join "`n") は改行で結合された文字列に置き換えられる。
#   注意点
#     ・開始の @" の後ろは、同じ行に何も書けない. すぐ改行する.
#     ・終了の "@ は、行頭(インデントなし)に書く必要がある. インデントすると終了と認識されない.
#   備考
#     ・シングルクォート版(@'...'@)は改行や引用符をそのまま含められる点は同じだが、変数展開や式展開はされない。
$toolCheckReport = @"
The $platformDisplayName development-tool check found items that need attention:

$($issues -join "`n")

Tell the user about these findings and show the applicable commands. Do not install or update anything automatically.
"@

# @{}はハッシュテーブルを作る構文。項目名と値を組にして保持する。
#     -Compress はJSONの余分な空白や改行を省き、1行のJSONにする指定
@{
    additionalContext = $toolCheckReport
} | ConvertTo-Json -Compress
