param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8887,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

# 切到项目根目录，避免相对路径错位
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 启动前先清理旧 uvicorn backend 进程树，避免新旧版本并存
$backendRootProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -ieq "python.exe" -and
        $_.CommandLine -and
        $_.CommandLine -match "backend\.app\.main:app" -and
        $_.CommandLine -match "uvicorn"
    }

if ($backendRootProcesses) {
    foreach ($rootProcess in $backendRootProcesses) {
        try {
            Write-Host "停止旧后端进程树 PID=$($rootProcess.ProcessId) ..."
            taskkill /PID $rootProcess.ProcessId /T /F | Out-Null
        } catch {
            Write-Host "停止进程树失败 PID=$($rootProcess.ProcessId)，继续尝试端口清理。"
        }
    }
    Start-Sleep -Milliseconds 800
}

# 再按端口兜底清理
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
