param(
    [int]$TimeoutSeconds = 30,
    [string]$LogPath = "$env:LOCALAPPDATA\LRAutomatic\logs\smart-previews.log"
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
function Log($message) {
    Add-Content -Path $LogPath -Value "$(Get-Date -Format o) $message" -Encoding UTF8
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Windows.Forms

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $process = $null
    do {
        $process = Get-Process | Where-Object { $_.ProcessName -match 'Lightroom' -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1
        if (-not $process) { Start-Sleep -Milliseconds 400 }
    } while (-not $process -and (Get-Date) -lt $deadline)
    if (-not $process) { throw 'Janela do Lightroom não encontrada.' }

    [Microsoft.VisualBasic.Interaction]::AppActivate($process.Id) | Out-Null
    Start-Sleep -Milliseconds 500

    # Lightroom Classic: Biblioteca > Visualizações > Criar visualizações inteligentes.
    # Envio por teclado é usado porque o SDK público não oferece esta operação.
    # As sequências podem ser sobrescritas por variáveis de ambiente para outras traduções.
    $menuKeys = if ($env:LRAUTOMATIC_SMART_PREVIEW_KEYS) { $env:LRAUTOMATIC_SMART_PREVIEW_KEYS } else { '%bvi' }
    [System.Windows.Forms.SendKeys]::SendWait($menuKeys)
    Start-Sleep -Milliseconds 800
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    Start-Sleep -Milliseconds 700
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    Log "Solicitação de Smart Previews enviada ao Lightroom PID=$($process.Id), keys=$menuKeys"
    exit 0
}
catch {
    Log "ERRO: $($_.Exception.Message)"
    exit 1
}
