<#
  READ-ONLY startup + RAM audit for Windows (changes nothing). Windows PowerShell 5.1 or PowerShell 7.
  Run:  powershell -NoProfile -ExecutionPolicy Bypass -File startup_audit.ps1 [-SaveBaseline <file.json>] [-Baseline <file.json>]

  Lists everything that starts with Windows, with its on/off state and exact location (so any change
  can be recorded and undone): Run keys + Task Manager's StartupApproved state, Startup folders,
  Store-app (MSIX) startup tasks, non-Microsoft logon/boot scheduled tasks, and auto-start services
  that are not part of Windows. Then RAM: total, available, and the biggest users grouped by name.

  -SaveBaseline writes the enabled items to a JSON file (keep it in the runs folder, not the skill).
  -Baseline compares against such a file and marks items NEW since then or now GONE.
  Turning items off is never done here: see references/windows.md, "Startup and RAM".

  StartupApproved first byte: odd (bit 0 set, e.g. 03/07) = disabled in Task Manager; 02/06 = enabled.
  MSIX StartupTask State: 2 or 4 = enabled; 0, 1, 3 = disabled.
#>
param([string]$SaveBaseline = '', [string]$Baseline = '')
$ErrorActionPreference = 'SilentlyContinue'
$zzItems = New-Object -TypeName System.Collections.Generic.List[object]

function Add-ZzItem([string]$zzKind, [string]$zzName, [bool]$zzOn, [string]$zzWhere, [string]$zzCommand) {
  $zzItems.Add([pscustomobject]@{ Kind = $zzKind; Name = $zzName; Enabled = $zzOn; Where = $zzWhere; Command = $zzCommand })
}

$zzRunPairs = @(
  @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'),
  @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'),
  @('HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32')
)
foreach ($zzPair in $zzRunPairs) {
  $zzVals = Get-ItemProperty -Path $zzPair[0]
  $zzApproved = Get-ItemProperty -Path $zzPair[1]
  if ($zzVals) {
    $zzVals.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object {
      $zzBytes = $zzApproved.($_.Name)
      $zzOn = (-not $zzBytes) -or (-not ($zzBytes[0] -band 1))
      Add-ZzItem 'Run key' $_.Name $zzOn $zzPair[0] ([string]$_.Value)
    }
  }
}
$zzFolders = @(
  @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup", 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'),
  @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup", 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder')
)
foreach ($zzPair in $zzFolders) {
  $zzApproved = Get-ItemProperty -Path $zzPair[1]
  Get-ChildItem -LiteralPath $zzPair[0] -File | Where-Object { $_.Name -ne 'desktop.ini' } | ForEach-Object {
    $zzBytes = $zzApproved.($_.Name)
    $zzOn = (-not $zzBytes) -or (-not ($zzBytes[0] -band 1))
    Add-ZzItem 'Startup folder' $_.Name $zzOn $zzPair[0] $_.FullName
  }
}
Get-ChildItem -Path 'HKCU:\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData' | ForEach-Object {
  $zzPkg = $_.PSChildName
  Get-ChildItem -Path $_.PSPath | ForEach-Object {
    $zzState = (Get-ItemProperty -Path $_.PSPath).State
    if ($null -ne $zzState) {
      Add-ZzItem 'Store app task' ("{0}\{1}" -f ($zzPkg -replace '_.*', ''), $_.PSChildName) ($zzState -eq 2 -or $zzState -eq 4) $_.Name ''
    }
  }
}
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
  $zzTask = $_
  $zzBoot = @($zzTask.Triggers | Where-Object { $_.CimClass.CimClassName -match 'Logon|Boot' })
  if ($zzBoot.Count) {
    $zzExe = ($zzTask.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)".Trim() }) -join ' ; '
    Add-ZzItem 'Logon/boot task' ($zzTask.TaskPath + $zzTask.TaskName) ($zzTask.State -ne 'Disabled') 'Task Scheduler' $zzExe
  }
}
Get-CimInstance -ClassName Win32_Service | Where-Object {
  $_.StartMode -eq 'Auto' -and $_.PathName -and $_.PathName -notmatch '\\Windows\\(System32|SysWOW64|Microsoft\.NET|servicing)\\' -and $_.PathName -notmatch 'Windows Defender'
} | ForEach-Object {
  Add-ZzItem 'Auto service' $_.Name $true ("service, now {0}" -f $_.State) $_.PathName
}

$zzBase = @()
if ($Baseline -and (Test-Path -LiteralPath $Baseline)) {
  # ForEach-Object unrolls the array: Windows PowerShell 5.1's ConvertFrom-Json emits a JSON array as ONE object
  $zzBase = @(Get-Content -LiteralPath $Baseline -Raw | ConvertFrom-Json | ForEach-Object { $_ })
}
$zzBaseKeys = @($zzBase | ForEach-Object { "$($_.Kind)|$($_.Name)" })

"=== STARTUP ITEMS (ON first)"
$zzItems | Sort-Object -Property @{ Expression = 'Enabled'; Descending = $true }, Kind, Name | ForEach-Object {
  $zzMark = ''
  if ($Baseline -and $_.Enabled -and ($zzBaseKeys -notcontains "$($_.Kind)|$($_.Name)")) { $zzMark = '  <- NEW since baseline' }
  "{0,-3} {1,-16} {2}{3}" -f $(if ($_.Enabled) { 'ON' } else { 'off' }), $_.Kind, $_.Name, $zzMark
  if ($_.Enabled -and $_.Command) { "      runs: {0}" -f $_.Command }
}
if ($Baseline) {
  $zzNowKeys = @($zzItems | Where-Object { $_.Enabled } | ForEach-Object { "$($_.Kind)|$($_.Name)" })
  $zzBase | Where-Object { $zzNowKeys -notcontains "$($_.Kind)|$($_.Name)" } | ForEach-Object { "GONE since baseline: {0} {1}" -f $_.Kind, $_.Name }
}
"on: {0} | off: {1}" -f @($zzItems | Where-Object { $_.Enabled }).Count, @($zzItems | Where-Object { -not $_.Enabled }).Count

if ($SaveBaseline) {
  $zzDir = Split-Path -Path $SaveBaseline -Parent
  if ($zzDir -and -not (Test-Path -LiteralPath $zzDir)) { New-Item -ItemType Directory -Path $zzDir -Force | Out-Null }
  @($zzItems | Where-Object { $_.Enabled }) | ConvertTo-Json -Depth 3 | Out-File -FilePath $SaveBaseline -Encoding utf8
  "baseline saved: $SaveBaseline"
}

"`n=== RAM"
$zzOs = Get-CimInstance -ClassName Win32_OperatingSystem
$zzTotal = $zzOs.TotalVisibleMemorySize / 1MB
$zzFree = $zzOs.FreePhysicalMemory / 1MB
"total {0:N1} GB | in use {1:N1} GB | available {2:N1} GB | up {3:%d}d {3:hh}h" -f $zzTotal, ($zzTotal - $zzFree), $zzFree, ((Get-Date) - $zzOs.LastBootUpTime)
$zzPerf = @{}
Get-CimInstance -ClassName Win32_PerfFormattedData_PerfProc_Process | ForEach-Object { $zzPerf[[int]$_.IDProcess] = [double]$_.WorkingSetPrivate }
"biggest RAM users (private working set, grouped by process name):"
Get-CimInstance -ClassName Win32_Process | Group-Object -Property Name | ForEach-Object {
  $zzSum = ($_.Group | ForEach-Object { $zzPerf[[int]$_.ProcessId] } | Measure-Object -Sum).Sum
  [pscustomobject]@{ Name = $_.Name; Count = $_.Count; MB = [math]::Round($zzSum / 1MB) }
} | Where-Object { $_.Name -notmatch '^(System Idle Process|_Total|Memory Compression)$' } | Sort-Object -Property MB -Descending |
  Select-Object -First 15 | ForEach-Object { "  {0,7} MB  x{1,-3} {2}" -f $_.MB, $_.Count, $_.Name }
if (Get-Process -Name vmmemWSL, vmmem -ErrorAction SilentlyContinue) { "  (vmmem / vmmemWSL = the WSL or Docker virtual machine; cap it in %USERPROFILE%\.wslconfig, see references/wsl.md)" }
