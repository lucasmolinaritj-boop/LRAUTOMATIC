@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LRAutomatic - Instalar servidor API

echo ==============================================
echo  LRAutomatic - Servidor API em segundo plano
echo ==============================================
echo.

fltmc >nul 2>&1
if errorlevel 1 (
  echo [INFO] Solicitando permissao de administrador...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

if not exist "instalar.bat" goto :erro_estrutura
if not exist "executar_servidor_api.bat" goto :erro_estrutura

echo [1/6] Instalando ou atualizando o LRAutomatic...
set "BASE_NO_PAUSE=%LRAUTOMATIC_NO_PAUSE%"
set "LRAUTOMATIC_NO_PAUSE=1"
call "instalar.bat"
set "INSTALL_RESULT=!ERRORLEVEL!"
set "LRAUTOMATIC_NO_PAUSE=%BASE_NO_PAUSE%"
if not "!INSTALL_RESULT!"=="0" goto :erro_instalacao

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :erro_python
if not exist "%CD%\config.json" goto :erro_config

echo [2/6] Validando API...
"%PYTHON_EXE%" -c "from lrautomatic.api import create_app; from lrautomatic.config import load_settings; s=load_settings(r'%CD%\config.json'); create_app(r'%CD%\config.json'); print('[OK] API carregada em', s.host, s.port)"
if errorlevel 1 goto :erro_api

rem Remove o servico nativo antigo que regrediu e pode disputar a porta.
echo [3/6] Removendo servidor antigo, se existir...
sc.exe stop LRAutomatic >nul 2>&1
"%PYTHON_EXE%" -m lrautomatic.windows_service remove >nul 2>&1
sc.exe delete LRAutomatic >nul 2>&1
schtasks /End /TN "LRAutomatic API" >nul 2>&1
schtasks /Delete /TN "LRAutomatic API" /F >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lrautomatic.cli serve*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [4/6] Registrando servidor invisivel no logon...
schtasks /Create /TN "LRAutomatic API" /SC ONLOGON /RL HIGHEST /TR "\"%CD%\executar_servidor_api.bat\"" /F >nul
if errorlevel 1 goto :erro_tarefa

echo [5/6] Iniciando servidor agora...
schtasks /Run /TN "LRAutomatic API" >nul
if errorlevel 1 goto :erro_inicio

set "API_PORT=45821"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$j=Get-Content -Raw -LiteralPath '%CD%\config.json' | ConvertFrom-Json; if($j.port){$j.port}else{45821}"`) do set "API_PORT=%%P"

echo [6/6] Testando API em http://127.0.0.1:!API_PORT!/health ...
set "HEALTH_OK="
for /l %%I in (1,1,25) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:!API_PORT!/health'; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set "HEALTH_OK=1"
    goto :health_ok
  )
  timeout /t 1 /nobreak >nul
)

:health_ok
if not defined HEALTH_OK goto :erro_health

echo.
echo ==============================================
echo  SERVIDOR API INSTALADO E FUNCIONANDO
echo ==============================================
echo  Modo: tarefa invisivel no logon
echo  API: http://127.0.0.1:!API_PORT!/health
echo ==============================================
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 0

:erro_estrutura
echo [ERRO] Arquivos obrigatorios nao foram encontrados nesta pasta.
goto :fim_erro
:erro_instalacao
echo [ERRO] A instalacao base falhou.
goto :fim_erro
:erro_python
echo [ERRO] Python da venv nao foi encontrado.
goto :fim_erro
:erro_config
echo [ERRO] config.json nao foi encontrado.
goto :fim_erro
:erro_api
echo [ERRO] O codigo da API nao pode ser carregado.
goto :fim_erro
:erro_tarefa
echo [ERRO] Nao foi possivel registrar a tarefa LRAutomatic API.
goto :fim_erro
:erro_inicio
echo [ERRO] A tarefa da API foi criada, mas nao iniciou.
goto :fim_erro
:erro_health
echo [ERRO] A API nao respondeu. Veja %%TEMP%%\lrautomatic-api-launcher.log
goto :fim_erro
:fim_erro
echo.
echo A instalacao do servidor nao foi concluida.
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 1
