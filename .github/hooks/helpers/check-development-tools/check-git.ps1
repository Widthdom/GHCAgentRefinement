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

# 冒頭のドットは、指定したスクリプトを現在のスコープで実行することを意味する。
#   ドットなしで実行すると、スクリプトは新しいスコープで実行され、その中で定義された関数や変数は終了時に破棄される。
. (Join-Path $PSScriptRoot 'common.ps1')

$resolvedPlatform = Get-DevelopmentPlatform -Platform $Platform

# $PSBoundParametersはPowerShellの自動変数で、現在の関数やスクリプトに渡された引数をハッシュテーブルとして保持している。
# 先頭の @ は スプラッティングを表す。配列やハッシュテーブルの中身を、コマンドの引数として展開して渡す。
#   例えば以下の行は、実質的には `Get-DevelopmentPackageManager -Platform AAA -BrewCommand BBB` に等しい。
$packageManager = Get-DevelopmentPackageManager @PSBoundParameters

$packageId = if ($resolvedPlatform -eq 'Windows') { 'Git.Git' } else { 'git' }

$installedGitVersion = Get-ToolVersion `
    -Command 'git' `
    -Arguments @('--version') `
    -Pattern 'git version (?<version>\d+(?:\.\d+){2})(?:\.windows\.(?<revision>\d+))?'

# GitのVersionが取得できなかった場合の処理。
if ($null -eq $installedGitVersion) {
    # パッケージマネージャの利用可否によってインストール方法の案内を変える。
    if ($null -eq $packageManager) {
        "- Git: not installed. No supported package manager is available for $resolvedPlatform, so no install command can be suggested."
    } else {
        $gitInstallCommand = Get-PackageInstallCommand -PackageManager $packageManager -PackageId $packageId
        "- Git: not installed. Install: $gitInstallCommand"
    }
    return
}

# 取得したGitのVersionを正規化し、revisionが含まれている場合はversionに統合する
if ($installedGitVersion.Match.Groups['revision'].Success) {
    $installedGitVersion.Value = ConvertTo-Version -Value (
        "$($installedGitVersion.Match.Groups['version'].Value).$($installedGitVersion.Match.Groups['revision'].Value)"
    )
}

# GitはインストールされていてVersionも取得できているが、パッケージマネージャが利用できない場合の処理。
# パッケージマネージャが利用できないとこの先の最新バージョンの確認ができないためここでreturnする。
if ($null -eq $packageManager) {
    "- Git: installed ($($installedGitVersion.Display)), but the latest version could not be checked because no supported package manager is available for $resolvedPlatform."
    return
}

$latest = Get-LatestPackageVersion -PackageManager $packageManager -PackageId $packageId

# パッケージマネージャによる最新バージョンが取得できなかった場合の処理。
if ($null -eq $latest) {
    "- Git: installed ($($installedGitVersion.Display)), but the latest version check failed."
    return
}

# -lt は less than の略。
if ($installedGitVersion.Value -lt $latest.Value) {
    $isGitManaged = Test-PackageManaged -PackageManager $packageManager -PackageId $packageId
    $gitInstallCommand = Get-PackageInstallCommand `
        -PackageManager $packageManager `
        -PackageId $packageId `
        -Upgrade:$isGitManaged

    "- Git: update available ($($installedGitVersion.Display) -> $($latest.Display)). Update: $gitInstallCommand"
}
