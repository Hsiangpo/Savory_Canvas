param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8887,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

# 切到项目根目录，避免相对路径错位
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 启动前先清理同端口旧进程，避免新旧版本并存
$listeningConnections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listeningConnections) {
    $owningProcessIds = $listeningConnections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($owningProcessId in $owningProcessIds) {
        try {
            $process = Get-Process -Id $owningProcessId -ErrorAction Stop
            Write-Host "停止旧后端进程 PID=$owningProcessId ($($process.ProcessName)) ..."
            Stop-Process -Id $owningProcessId -Force -ErrorAction Stop
        } catch {
            Write-Host "跳过 PID=$owningProcessId（进程已退出或无权限）"
        }
    }
    Start-Sleep -Milliseconds 800
}

# 二次确认端口是否释放
$stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stillListening) {
    throw "端口 $Port 仍被占用，请以管理员身份重试。"
}

# 启动 uvicorn
$uvicornArgs = @(
    "-m", "uvicorn",
    "backend.app.main:app",
    "--app-dir", ".",
    "--host", $Host,
    "--port", "$Port"
)
if (-not $NoReload) {
    $uvicornArgs += "--reload"
}

Write-Host "启动后端：python $($uvicornArgs -join ' ')"
python @uvicornArgs
