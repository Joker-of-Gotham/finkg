<#
.SYNOPSIS
    finkg 安装器（Windows 包装）。真正的逻辑在 install.py，所有参数原样透传。

.EXAMPLE
    .\install.ps1                # 交互
    .\install.ps1 --all          # 装到所有检测到的 agent
    .\install.ps1 --check        # 只校验

.NOTES
    也可以一行装完（不 clone）：
    iwr -useb https://raw.githubusercontent.com/Joker-of-Gotham/finkg/main/install.ps1 | iex
#>
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$repoUrl = "https://github.com/Joker-of-Gotham/finkg.git"
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

$py = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break }
}
if (-not $py) { throw "需要 Python 3.9 或更新版本，但没找到 python/python3/py" }

# 走 iwr | iex 时脚本不在仓库里，先把仓库取到临时目录
if (-not (Test-Path (Join-Path $here "install.py"))) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "需要 git" }
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("finkg-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    Write-Host "正在获取 finkg …"
    git clone --depth 1 $repoUrl $tmp 2>&1 | Out-Null
    $here = $tmp
}

& $py (Join-Path $here "install.py") @args
exit $LASTEXITCODE
