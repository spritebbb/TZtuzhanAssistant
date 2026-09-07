# TZtuzhanAssistant 每小时时间 tick 计划任务安装器（P1-04）
# 用法: .\scripts\install_time_tick.ps1 install|status|uninstall|dry-run
# 任务名固定 TZtuzhanAssistant-TimeTick；重复安装只更新同一项；卸载只移除
# 本项目准确名称的任务，不触碰其它任务。窗口默认隐藏。
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'status', 'uninstall', 'dry-run')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$TaskName = 'TZtuzhanAssistant-TimeTick'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$DataRoot = Join-Path $ProjectRoot 'data'

if (-not (Test-Path $Python)) {
    Write-Error "找不到虚拟环境 Python: $Python"
    exit 1
}

$ActionArgs = "-m backend.maintenance.time_tick --data-root `"$DataRoot`" --json"
$Description = 'TZtuzhanAssistant 每小时行程状态推进（P1-04 time tick）'

function Get-ExistingTask {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

switch ($Action) {
    'dry-run' {
        Write-Output "任务名:   $TaskName"
        Write-Output "程序:     $Python"
        Write-Output "参数:     $ActionArgs"
        Write-Output "工作目录: $ProjectRoot"
        Write-Output "触发器:   每小时（重复间隔 1 小时）"
        Write-Output "数据根:   $DataRoot"
    }
    'status' {
        $task = Get-ExistingTask
        if ($null -eq $task) {
            Write-Output "未安装（任务名 $TaskName 不存在）"
        } else {
            $info = $task | Get-ScheduledTaskInfo
            Write-Output "已安装: $($task.State)，上次运行: $($info.LastRunTime)，结果: $($info.LastTaskResult)，下次: $($info.NextRunTime)"
        }
    }
    'uninstall' {
        $task = Get-ExistingTask
        if ($null -eq $task) {
            Write-Output "无需卸载（任务不存在）"
        } else {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output "已卸载 $TaskName"
        }
    }
    'install' {
        # 重复安装：先移除同名再注册 = 始终只有同一项且配置最新
        $existing = Get-ExistingTask
        if ($null -ne $existing) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output "已更新既有任务（同名重建）"
        }
        $action = New-ScheduledTaskAction -Execute $Python -Argument $ActionArgs -WorkingDirectory $ProjectRoot
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Hours 1) `
            -RepetitionDuration ([TimeSpan]::MaxValue)
        $settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
            -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Description $Description | Out-Null
        Write-Output "已安装 $TaskName（每小时，隐藏窗口，错过的周期启动时补跑）"
    }
}
