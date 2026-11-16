param(
    [string]$Python = "python",
    [string]$BackendRoot = (Join-Path $PSScriptRoot '..\backend'),
    [switch]$SkipKetangpai,
    [switch]$Offline
)

$ErrorActionPreference = 'Stop'
$BackendRoot = [System.IO.Path]::GetFullPath($BackendRoot)
$venvPython = Join-Path $BackendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv (Join-Path $BackendRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败，请用 -Python 指定真实的 Python 3.10+ 解释器。' }
}
$pipOptions = @()
if ($Offline) { $pipOptions += '--no-index' }
& $venvPython -m pip install @pipOptions -r (Join-Path $BackendRoot 'requirements-csharp-desktop.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败。' }
if (-not $SkipKetangpai) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw '课堂派需要 Node.js 18+ 和 pnpm，请安装到 D:\nodejs 并加入 PATH 后重试。'
    }
    Push-Location (Join-Path $BackendRoot 'integrations\ketangpai')
    try {
        $pnpmOptions = @('install', '--frozen-lockfile')
        if ($Offline) { $pnpmOptions += '--offline' }
        & pnpm @pnpmOptions
        if ($LASTEXITCODE -ne 0) { throw '课堂派依赖安装失败。' }
        & pnpm exec tsc --noEmit
        if ($LASTEXITCODE -ne 0) { throw '课堂派 TypeScript 检查失败。' }
    }
    finally { Pop-Location }
}
Write-Output "后端环境就绪：$venvPython"
