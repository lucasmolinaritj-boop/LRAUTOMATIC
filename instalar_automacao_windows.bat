@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LRAutomatic - Instalar automacao completa

echo ================================================
echo  LRAutomatic - Automacao completa Home Picz
echo ================================================
echo.

rem A automacao completa exige privilegios administrativos para o servico
rem e para a tarefa agendada com nivel elevado.
fltmc >nul 2>&1
if errorlevel 1 (
  echo [INFO] Solicitando permissao de administrador...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath $env:ComSpec -ArgumentList '/d','/c','""%~f0""' -Verb RunAs"
  exit /b
)

if not exist "instalar_servidor.bat" goto :erro_estrutura

rem O instalador do servidor ja instala/atualiza pacote, dependencias e API.
echo [1/5] Instalando ou atualizando aplicativo e servidor...
set "LRAUTOMATIC_NO_PAUSE=1"
call "instalar_servidor.bat"
set "SERVER_RESULT=!ERRORLEVEL!"
set "LRAUTOMATIC_NO_PAUSE="
if not "!SERVER_RESULT!"=="0" goto :erro_servidor

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "PYTHONW_EXE=%CD%\.venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" goto :erro_python
if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"
if not exist "%CD%\config.json" goto :erro_config

echo [2/5] Validando agente Home Picz...
"%PYTHON_EXE%" -c "import lrautomatic.session_agent; print('[OK] Agente de sessao carregado.')"
if errorlevel 1 goto :erro_agente

rem Encerra qualquer copia anterior para impedir agentes duplicados.
echo [3/5] Encerrando agente antigo, se existir...
schtasks /End /TN "LRAutomatic Session Agent" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lrautomatic.session_agent*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

rem O PowerShell registra a tarefa sem os problemas de aspas do schtasks /TR.
echo [4/5] Registrando automacao no logon do Windows...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $name='LRAutomatic Session Agent'; $exe='%PYTHONW_EXE%'; $cfg='%CD%\config.json'; $wd='%CD%'; Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue; $action=New-ScheduledTaskAction -Execute $exe -Argument ('-m lrautomatic.session_agent "' + $cfg + '"') -WorkingDirectory $wd; $trigger=New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero); $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest; Register-ScheduledTask -TaskName $name -Description 'Agente Home Picz do LRAutomatic' -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null"
if errorlevel 1 goto :erro_tarefa

echo [5/5] Iniciando automacao agora...
schtasks /Run /TN "LRAutomatic Session Agent" >nul
if errorlevel 1 goto :erro_inicio

timeout /t 2 /nobreak >nul
schtasks /Query /TN "LRAutomatic Session Agent" /FO LIST | findstr /I /C:"Status:" /C:"Estado:"

echo.
echo ================================================
echo  AUTOMACAO COMPLETA INSTALADA
echo ================================================
echo  Aplicativo: instalado e atualizado
echo  Servidor API: servico automatico do Windows
echo  Agente Home Picz: inicia em cada logon
echo  Catalogo/Lightroom: controlados pelo agente
echo.
echo  Uso normal: abrir_app.bat
echo  Reparar apenas API: instalar_servidor.bat
echo  Remover tudo: desinstalar_automacao_windows.bat
echo ================================================
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 0

:erro_estrutura
echo [ERRO] instalar_servidor.bat nao foi encontrado nesta pasta.
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
goto :fim_erro
:erro_inicio
echo [ERRO] A tarefa foi criada, mas nao iniciou.
goto :fim_erro
:fim_erro
echo.
echo A instalacao da automacao nao foi concluida.
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 1
