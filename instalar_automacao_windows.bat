@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LRAutomatic - Instalar automacao completa

echo ================================================
echo  LRAutomatic - Automacao completa Home Picz
echo ================================================
echo.

rem A automacao completa exige privilegios administrativos para o servico
a rem e para a tarefa agendada com nivel elevado.
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
set "LRAUTOMATIC_NO_PAUSE="
if errorlevel 1 goto :erro_servidor

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

rem Um XML evita os problemas de aspas do /TR e permite reinicio automatico.
echo [4/5] Registrando automacao no logon do Windows...
set "TASK_XML=%TEMP%\lrautomatic-session-agent-%RANDOM%.xml"
"%PYTHON_EXE%" -c "from pathlib import Path; import html, os; root=Path(r'%CD%'); exe=root/'.venv/Scripts/pythonw.exe'; exe=exe if exe.exists() else root/'.venv/Scripts/python.exe'; args='-m lrautomatic.session_agent '+chr(34)+str(root/'config.json')+chr(34); xml='''<?xml version=\"1.0\" encoding=\"UTF-16\"?>\n<Task version=\"1.4\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">\n  <RegistrationInfo><Description>Agente Home Picz do LRAutomatic.</Description></RegistrationInfo>\n  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>\n  <Principals><Principal id=\"Author\"><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>\n  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>7</Priority><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>\n  <Actions Context=\"Author\"><Exec><Command>'''+html.escape(str(exe))+'''</Command><Arguments>'''+html.escape(args)+'''</Arguments><WorkingDirectory>'''+html.escape(str(root))+'''</WorkingDirectory></Exec></Actions>\n</Task>'''; Path(os.environ['TASK_XML']).write_text(xml, encoding='utf-16')"
if errorlevel 1 goto :erro_tarefa
schtasks /Create /TN "LRAutomatic Session Agent" /XML "%TASK_XML%" /F >nul
set "TASK_RESULT=!ERRORLEVEL!"
del /q "%TASK_XML%" >nul 2>&1
if not "!TASK_RESULT!"=="0" goto :erro_tarefa

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
