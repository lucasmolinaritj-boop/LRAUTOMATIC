@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LRAutomatic - Instalar servidor

echo ==============================================
echo  LRAutomatic - Instalador do servidor Windows
echo ==============================================
echo.

rem O servico precisa de privilegios administrativos.
fltmc >nul 2>&1
if errorlevel 1 (
  echo [INFO] Solicitando permissao de administrador...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

if not exist "instalar.bat" goto :erro_estrutura

rem Instala/atualiza o pacote antes de registrar o servico.
echo [1/7] Instalando ou atualizando o LRAutomatic...
set "BASE_NO_PAUSE=%LRAUTOMATIC_NO_PAUSE%"
set "LRAUTOMATIC_NO_PAUSE=1"
call "instalar.bat"
set "INSTALL_RESULT=!ERRORLEVEL!"
set "LRAUTOMATIC_NO_PAUSE=%BASE_NO_PAUSE%"
if not "!INSTALL_RESULT!"=="0" goto :erro_instalacao

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :erro_python
if not exist "%CD%\config.json" goto :erro_config

rem Valida tanto o pacote quanto os modulos exclusivos do Windows.
echo [2/7] Validando dependencias do servico...
"%PYTHON_EXE%" -c "import lrautomatic.windows_service, servicemanager, win32serviceutil, uvicorn; print('[OK] Dependencias do servico carregadas.')"
if errorlevel 1 goto :erro_dependencias

rem Remove a instalacao anterior para evitar ImagePath apontando para outra venv.
echo [3/7] Removendo registro antigo, se existir...
sc.exe query LRAutomatic >nul 2>&1
if errorlevel 1 goto :registrar_servico

sc.exe stop LRAutomatic >nul 2>&1
timeout /t 4 /nobreak >nul
"%PYTHON_EXE%" -m lrautomatic.windows_service remove >nul 2>&1
if errorlevel 1 sc.exe delete LRAutomatic >nul 2>&1
timeout /t 2 /nobreak >nul

:registrar_servico
rem Registra novamente usando o Python da venv atual.
echo [4/7] Registrando servico LRAutomatic...
"%PYTHON_EXE%" -m lrautomatic.windows_service --startup auto install
if errorlevel 1 goto :erro_registro

rem O servico nao pode depender do diretorio atual, normalmente System32.
echo [5/7] Registrando caminho fixo da configuracao...
reg.exe add "HKLM\SYSTEM\CurrentControlSet\Services\LRAutomatic\Parameters" ^
  /v ConfigPath /t REG_SZ /d "%CD%\config.json" /f >nul
if errorlevel 1 goto :erro_registro_config

rem Inicializacao automatica atrasada reduz conflitos durante o boot.
sc.exe config LRAutomatic start= delayed-auto >nul
sc.exe failure LRAutomatic reset= 86400 actions= restart/5000/restart/15000/restart/60000 >nul
sc.exe failureflag LRAutomatic 1 >nul 2>&1

set "API_HOST=127.0.0.1"
set "API_PORT=45821"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$j=Get-Content -Raw -LiteralPath '%CD%\config.json' | ConvertFrom-Json; if($j.port){$j.port}else{45821}"`) do set "API_PORT=%%P"

echo [6/7] Iniciando servico...
sc.exe start LRAutomatic >nul
if errorlevel 1 goto :erro_inicio

echo [7/7] Testando API em http://%API_HOST%:%API_PORT%/health ...
set "HEALTH_OK="
for /l %%I in (1,1,20) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://%API_HOST%:%API_PORT%/health'; if ($r.StatusCode -eq 200) { exit 0 }; exit 1 } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set "HEALTH_OK=1"
    goto :health_pronto
  )
  timeout /t 1 /nobreak >nul
)

:health_pronto
if not defined HEALTH_OK goto :erro_health

echo.
echo ==============================================
echo  SERVIDOR INSTALADO E FUNCIONANDO
echo ==============================================
echo Servico: LRAutomatic
echo Inicio: automatico atrasado
echo Config: %CD%\config.json
echo API: http://%API_HOST%:%API_PORT%/health
echo.
sc.exe query LRAutomatic | findstr /I "STATE"
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 0

:erro_estrutura
echo [ERRO] instalar.bat nao foi encontrado nesta pasta.
goto :fim_erro
:erro_instalacao
echo [ERRO] A instalacao base do LRAutomatic falhou.
goto :fim_erro
:erro_python
echo [ERRO] Python da venv nao encontrado em .venv\Scripts\python.exe.
goto :fim_erro
:erro_config
echo [ERRO] config.json nao existe. Execute instalar.bat e abra o aplicativo ao menos uma vez.
goto :fim_erro
:erro_dependencias
echo [ERRO] pywin32 ou outra dependencia do servico nao foi carregada.
goto :fim_erro
:erro_registro
echo [ERRO] Nao foi possivel registrar o servico do Windows.
goto :fim_erro
:erro_registro_config
echo [ERRO] Nao foi possivel registrar o caminho do config.json.
goto :fim_erro
:erro_inicio
echo [ERRO] O Windows recusou a inicializacao do servico.
goto :mostrar_diagnostico
:erro_health
echo [ERRO] O servico iniciou, mas a API nao respondeu no teste.
:mostrar_diagnostico
echo.
echo Estado atual:
sc.exe query LRAutomatic
echo.
echo Eventos recentes do servico:
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-10)} -ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'Python|LRAutomatic' -or $_.Message -match 'LRAutomatic' } | Select-Object -First 8 TimeCreated,LevelDisplayName,Message | Format-List"
echo.
echo Verifique tambem o arquivo windows-service.log dentro da pasta de logs configurada.
goto :fim_erro
:fim_erro
echo.
echo A instalacao nao foi concluida.
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 1
