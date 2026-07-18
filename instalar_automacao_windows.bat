@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LRAutomatic - Instalar automacao completa

echo ================================================
echo  LRAutomatic - Automacao completa Home Picz
echo ================================================
echo.

rem A automacao completa exige privilegios administrativos.
fltmc >nul 2>&1
if errorlevel 1 (
  echo [INFO] Solicitando permissao de administrador...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

if not exist "instalar_servidor.bat" goto :erro_estrutura
if not exist "executar_agente_homepicz.bat" goto :erro_lancador
if not exist "lightroom_plugin\LRAutomatic.lrplugin\Info.lua" goto :erro_plugin

rem O instalador do servidor ja instala/atualiza pacote, dependencias e API.
echo [1/7] Instalando ou atualizando aplicativo e servidor...
set "LRAUTOMATIC_NO_PAUSE=1"
call "instalar_servidor.bat"
set "SERVER_RESULT=!ERRORLEVEL!"
set "LRAUTOMATIC_NO_PAUSE="
if not "!SERVER_RESULT!"=="0" goto :erro_servidor

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :erro_python
if not exist "%CD%\config.json" goto :erro_config

rem Instala o plugin em uma pasta estavel reconhecida pelo Lightroom.
echo [2/7] Instalando ou atualizando plugin do Lightroom...
set "LR_MODULES=%APPDATA%\Adobe\Lightroom\Modules"
set "LR_PLUGIN_DEST=!LR_MODULES!\LRAutomatic.lrplugin"
if not exist "!LR_MODULES!" mkdir "!LR_MODULES!"
if exist "!LR_PLUGIN_DEST!" rmdir /s /q "!LR_PLUGIN_DEST!"
robocopy "%CD%\lightroom_plugin\LRAutomatic.lrplugin" "!LR_PLUGIN_DEST!" /E /NFL /NDL /NJH /NJS /NC /NS >nul
set "ROBO_RESULT=!ERRORLEVEL!"
if !ROBO_RESULT! GEQ 8 goto :erro_copia_plugin
if not exist "!LR_PLUGIN_DEST!\Info.lua" goto :erro_copia_plugin

echo [3/7] Validando agente Home Picz...
"%PYTHON_EXE%" -c "import lrautomatic.session_agent; print('[OK] Agente de sessao carregado.')"
if errorlevel 1 goto :erro_agente

rem Encerra qualquer copia anterior para impedir agentes duplicados.
echo [4/7] Encerrando agente antigo, se existir...
schtasks /End /TN "LRAutomatic Session Agent" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lrautomatic.session_agent*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

rem Remove tarefa antiga antes de recriar.
echo [5/7] Removendo tarefa antiga, se existir...
schtasks /Delete /TN "LRAutomatic Session Agent" /F >nul 2>&1

rem Registra tarefa apontando para um BAT simples, sem linha complexa de argumentos.
echo [6/7] Registrando automacao no logon do Windows...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='LRAutomatic Session Agent'; $launcher='%CD%\executar_agente_homepicz.bat'; $action=New-ScheduledTaskAction -Execute $launcher -WorkingDirectory '%CD%'; $trigger=New-ScheduledTaskTrigger -AtLogOn; $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero); $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest; Register-ScheduledTask -TaskName $name -Description 'Agente Home Picz do LRAutomatic' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null"
if errorlevel 1 goto :erro_tarefa

echo [7/7] Iniciando automacao agora...
schtasks /Run /TN "LRAutomatic Session Agent" >nul
if errorlevel 1 goto :erro_inicio

timeout /t 3 /nobreak >nul
schtasks /Query /TN "LRAutomatic Session Agent" /FO LIST | findstr /I /C:"Status:" /C:"Estado:" /C:"Last Run Result:" /C:"Resultado da Ultima Execucao:"

echo.
echo ================================================
echo  AUTOMACAO COMPLETA INSTALADA
echo ================================================
echo  Aplicativo: instalado e atualizado
echo  Plugin Lightroom: !LR_PLUGIN_DEST!
echo  Servidor API: servico automatico do Windows
echo  Agente Home Picz: inicia em cada logon
echo  Catalogo/Lightroom: controlados pelo agente
echo.
echo  IMPORTANTE: feche e abra o Lightroom para carregar o plugin atualizado.
echo.
echo  Uso normal: abrir_app.bat
echo  Reparar apenas API: instalar_servidor.bat
echo  Remover tudo: desinstalar_automacao_windows.bat
echo ================================================
pause
exit /b 0

:erro_estrutura
echo [ERRO] instalar_servidor.bat nao foi encontrado nesta pasta.
goto :fim_erro
:erro_lancador
echo [ERRO] executar_agente_homepicz.bat nao foi encontrado nesta pasta.
goto :fim_erro
:erro_plugin
echo [ERRO] A pasta lightroom_plugin\LRAutomatic.lrplugin esta incompleta.
goto :fim_erro
:erro_copia_plugin
echo [ERRO] Nao foi possivel instalar o plugin em:
echo        !LR_PLUGIN_DEST!
goto :fim_erro
:erro_servidor
echo [ERRO] A instalacao do aplicativo ou servidor falhou.
goto :fim_erro
:erro_python
echo [ERRO] Python da venv nao foi encontrado.
goto :fim_erro
:erro_config
echo [ERRO] config.json nao foi encontrado.
goto :fim_erro
:erro_agente
echo [ERRO] O modulo do agente Home Picz nao pode ser carregado.
goto :fim_erro
:erro_tarefa
echo [ERRO] Nao foi possivel criar a tarefa agendada do agente.
goto :mostrar_diagnostico
:erro_inicio
echo [ERRO] A tarefa foi criada, mas nao iniciou.
goto :mostrar_diagnostico

:mostrar_diagnostico
echo.
echo Estado da tarefa:
schtasks /Query /TN "LRAutomatic Session Agent" /V /FO LIST
echo.
echo Eventos recentes do Agendador de Tarefas:
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 20 -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'LRAutomatic Session Agent' } | Select-Object -First 8 TimeCreated,Id,LevelDisplayName,Message | Format-List"
echo.
echo Log auxiliar, se existir: %%TEMP%%\lrautomatic-agent-launcher.log

goto :fim_erro

:fim_erro
echo.
echo A instalacao da automacao nao foi concluida.
pause
exit /b 1