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
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath $env:ComSpec -ArgumentList '/d','/c','""%~f0""' -Verb RunAs"
  exit /b
)

if not exist "instalar.bat" goto :erro_estrutura

rem Instala/atualiza o pacote antes de registrar o servico.
echo [1/7] Instalando ou atualizando o LRAutomatic...
call "instalar.bat"
if errorlevel 1 goto :erro_instalacao

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :erro_python
if not exist "%CD%\config.json" goto :erro_config

rem Valida tanto o pacote quanto os modulos exclusivos do Windows.
echo [2/7] Validando dependencias do servico...
"%PYTHON_EXE%" -c "import lrautomatic.windows_service, servicemanager, win32serviceutil, uvicorn; print('[OK] Dependencias do servico carregadas.')"
if errorlevel 1 goto :erro_dependencias

rem Executa o pos-instalador do pywin32 quando estiver disponivel.
for /f "delims=" %%F in ('dir /b /s "%CD%\.venv\Scripts\pywin32_postinstall.py" 2^>nul') do (
  "%PYTHON_EXE%" "%%F" -install >nul 2>&1
  goto :pywin32_pronto
)
:pywin32_pronto

rem Remove a instalacao anterior para evitar ImagePath apontando para outra venv.
echo [3/7] Removendo registro antigo, se existir...
sc.exe query LRAutomatic >nul 2>&1
if not errorlevel 1 (
  sc.exe stop LRAutomatic >nul 2>&1
  for /l %%I in (1,1,20) do (
    sc.exe query LRAutomatic 2>nul | find /I "STOPPED" >nul && goto :servico_parado
    timeout /t 1 /nobreak >nul
  )
  :servico_parado
  "%PYTHON_EXE%" -m lrautomatic.windows_service remove >nul 2>&1
  if errorlevel 1 sc.exe delete LRAutomatic >nul 2>&1
  timeout /t 2 /nobreak >nul
)

rem Registra novamente usando o Python da venv atual.
echo [4/7] Registrando servico LRAutomatic...
"%PYTHON_EXE%" -m lrautomatic.windows_service --startup auto install
if errorlevel 1 goto :erro_registro

rem O servico nao pode depender do diretorio atual (normalmente System32).
rem Gravamos explicitamente o config.json no registro do proprio servico.
echo [5/7] Registrando caminho fixo da configuracao...
reg.exe add "HKLM\SYSTEM\CurrentControlSet\Services\LRAutomatic\Parameters" ^
  /v ConfigPath /t REG_SZ /d "%CD%\config.json" /f >nul
if errorlevel 1 goto :erro_registro_config

rem Inicializacao automatica atrasada reduz conflitos durante o boot.
sc.exe config LRAutomatic start= delayed-auto >nul
sc.exe failure LRAutomatic reset= 86400 actions= restart/5000/restart/15000/restart/60000 >nul
sc.exe failureflag LRAutomatic 1 >nul 2>&1

rem Le host e porta diretamente do config para testar a instalacao real.
for /f "tokens=1,2 delims=|" %%A in ('"%PYTHON_EXE%" -c "import json,pathlib; d=json.loads(pathlib.Path(r'%CD%\config.json').read_text(encoding='utf-8-sig')); print(str(d.get('host','127.0.0.1'))+'|'+str(d.get('port',45821)))"') do (
  set "API_HOST=%%A"
  set "API_PORT=%%B"
)
if not defined API_HOST set "API_HOST=127.0.0.1"
if "%API_HOST%"=="0.0.0.0" set "API_HOST=127.0.0.1"
if not defined API_PORT set "API_PORT=45821"

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
pause
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
echo Verifique tambem:
echo   %%LOCALAPPDATA%%\LRAutomatic\logs\windows-service.log
echo   config.json

goto :fim_erro

:fim_erro
echo.
echo A instalacao nao foi concluida.
pause
exit /b 1
