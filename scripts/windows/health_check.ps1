<#
  READ-ONLY health check for Windows (changes nothing). Windows PowerShell 5.1 or PowerShell 7.
  Run:  powershell -NoProfile -ExecutionPolicy Bypass -File health_check.ps1 [-Months 12]

  Covers: free space per drive, each physical disk's health (plus reliability counters when the
  shell is elevated), storage and crash events counted per month, WSL distros and their disk
  files, and recent WSL crash dumps. How to read it: references/windows.md, "Health".

  Variable names use a $zz prefix on purpose: PowerShell variables are case-insensitive, and short
  names like $t / $T have collided before in cleanup scripts.
#>
param([int]$Months = 12)
$ErrorActionPreference = 'SilentlyContinue'
$zzSince = (Get-Date).AddMonths(-$Months)

"=== FREE SPACE"
Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
  if ($_.Size) { "{0}  {1:N1} GB free of {2:N0} GB ({3:N0}% free)" -f $_.DeviceID, ($_.FreeSpace / 1GB), ($_.Size / 1GB), (100 * $_.FreeSpace / $_.Size) }
}

"`n=== DISKS"
foreach ($zzDisk in @(Get-PhysicalDisk)) {
  "{0} | {1} | bus={2} | health={3} | status={4} | {5:N0} GB" -f $zzDisk.FriendlyName, $zzDisk.MediaType, $zzDisk.BusType,
    $zzDisk.HealthStatus, ($zzDisk.OperationalStatus -join ','), ($zzDisk.Size / 1GB)
  try {
    $zzRel = $zzDisk | Get-StorageReliabilityCounter -ErrorAction Stop
    "   temperature={0}C wear={1}% readErrors={2} writeErrors={3} powerOnHours={4}" -f $zzRel.Temperature, $zzRel.Wear,
      $zzRel.ReadErrorsTotal, $zzRel.WriteErrorsTotal, $zzRel.PowerOnHours
  } catch {
    "   reliability counters: need an elevated shell (only worth it if the events below look bad)"
  }
}
if (Get-Command -Name smartctl -ErrorAction SilentlyContinue) { "smartctl is installed: for full SMART data run 'smartctl -a <disk>' in an elevated shell" }

"`n=== STORAGE AND CRASH EVENTS since {0:yyyy-MM-dd} (count per month)" -f $zzSince
$zzQueries = @(
  @{ Label = 'Storage controller resets (Event 129: stornvme / storahci / RAID drivers)'; Filter = @{ LogName = 'System'; Id = 129; StartTime = $zzSince }; Provider = 'stor|nvme|ahci|iaStor|raid|scsi' },
  @{ Label = 'Disk errors (disk 7 bad block, 51 paging error, 153 retried IO)'; Filter = @{ LogName = 'System'; ProviderName = 'disk'; Id = 7, 51, 153; StartTime = $zzSince }; Provider = '' },
  @{ Label = 'File system corruption (Ntfs 55 / 98, warnings and errors only)'; Filter = @{ LogName = 'System'; ProviderName = 'Ntfs', 'Microsoft-Windows-Ntfs'; Id = 55, 98; Level = 1, 2, 3; StartTime = $zzSince }; Provider = '' },
  @{ Label = 'Unexpected restarts (Kernel-Power 41)'; Filter = @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power'; Id = 41; StartTime = $zzSince }; Provider = '' },
  @{ Label = 'Unexpected shutdowns (EventLog 6008)'; Filter = @{ LogName = 'System'; ProviderName = 'EventLog'; Id = 6008; StartTime = $zzSince }; Provider = '' },
  @{ Label = 'Blue screens (BugCheck 1001)'; Filter = @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WER-SystemErrorReporting'; Id = 1001; StartTime = $zzSince }; Provider = '' },
  @{ Label = 'Hardware errors (WHEA-Logger, errors only)'; Filter = @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; Level = 1, 2; StartTime = $zzSince }; Provider = '' }
)
foreach ($zzQ in $zzQueries) {
  $zzEvents = @(Get-WinEvent -FilterHashtable $zzQ.Filter -ErrorAction SilentlyContinue)
  if ($zzQ.Provider) { $zzEvents = @($zzEvents | Where-Object { $_.ProviderName -match $zzQ.Provider }) }
  $zzByMonth = ($zzEvents | Group-Object -Property { $_.TimeCreated.ToString('yyyy-MM') } | Sort-Object -Property Name |
      ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ' '
  "{0}: total {1}  {2}" -f $zzQ.Label, $zzEvents.Count, $zzByMonth
  if ($zzQ.Label -like 'Unexpected restarts*' -and $zzEvents.Count) {
    $zzEvents | Select-Object -First 5 | ForEach-Object {
      $zzCode = ([xml]$_.ToXml()).Event.EventData.Data | Where-Object { $_.Name -eq 'BugcheckCode' } | ForEach-Object { $_.'#text' }
      "   {0:yyyy-MM-dd HH:mm}  BugcheckCode={1}  (0 = hard freeze or power loss, not a blue screen)" -f $_.TimeCreated, $zzCode
    }
  }
}

"`n=== WSL"
if (Get-Command -Name wsl.exe -ErrorAction SilentlyContinue) {
  $env:WSL_UTF8 = '1'
  & wsl.exe --list --verbose 2>$null | Where-Object { $_.Trim() } | ForEach-Object { "  $_" }
  Get-ChildItem -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object {
    $zzProps = Get-ItemProperty -Path $_.PSPath
    if ($zzProps.BasePath) {
      $zzVhd = Join-Path -Path ($zzProps.BasePath -replace '^\\\\\?\\', '') -ChildPath 'ext4.vhdx'
      $zzSize = if (Test-Path -LiteralPath $zzVhd) { '{0:N1} GB' -f ((Get-Item -LiteralPath $zzVhd).Length / 1GB) } else { '?' }
      "  {0}: disk file {1}  ({2})" -f $zzProps.DistributionName, $zzSize, $zzVhd
    }
  }
  $zzDumps = @(Get-ChildItem -LiteralPath (Join-Path -Path $env:TEMP -ChildPath 'wsl-crashes') -File | Where-Object { $_.LastWriteTime -gt $zzSince })
  $zzNote = ''
  if ($zzDumps.Count) { $zzNote = "  newest: $(($zzDumps | Sort-Object -Property LastWriteTime | Select-Object -Last 1).Name)  <- check for ext4 corruption (references/wsl.md)" }
  "  WSL crash dumps since cutoff: {0}{1}" -f $zzDumps.Count, $zzNote
} else {
  "  WSL not installed"
}

"`n=== MEMORY"
$zzOs = Get-CimInstance -ClassName Win32_OperatingSystem
"  total {0:N1} GB | available {1:N1} GB | up {2:%d}d {2:hh}h" -f ($zzOs.TotalVisibleMemorySize / 1MB), ($zzOs.FreePhysicalMemory / 1MB), ((Get-Date) - $zzOs.LastBootUpTime)
"  (startup apps and the biggest RAM users: startup_audit.ps1)"
