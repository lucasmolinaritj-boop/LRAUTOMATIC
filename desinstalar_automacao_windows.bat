@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================
echo  DESINSTALADOR LRAUTOMATIC
echo ================================================
echo.
echo Este processo remove:
echo  - servico da API do Windows
echo  - tarefa agendada do agente Home Picz
echo  - processos de automacao ainda ativos
echo.
echo NAO remove fotos nem catalogos do Lightroom.
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Execute este arquivo como Administrador.
  pause
  exit /b 1
)

set /p CONFIRM=Digite SIM para continuar: 
if /I not "%CONFIRM%"=="SIM" (
  echo Desinstalacao cancelada.
  pause
  exit /b 0
)

echo.
echo [1/4] Encerrando agente de sessao...
schtasks /End /TN "LRAutomatic Session Agent" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -like '*lrautomatic.session_agent*' } ^| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [2/4] Removendo tarefa agendada...
schtasks /Delete /TN "LRAutomatic Session Agent" /F >nul 2>&1

echo [3/4] Parando e removendo servico da API...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m lrautomatic.windows_service stop >nul 2>&1
  ".venv\Scripts\python.exe" -m lrautomatic.windows_service remove >nul 2>&1
) else (
  sc stop LRAutomatic >nul 2>&1
  sc delete LRAutomatic >nul 2>&1
)

echo [4/4] Limpando marcadores de execucao...
if exist "%LOCALAPPDATA%\LRAutomatic\control\startup_flow.json" del /q "%LOCALAPPDATA%\LRAutomatic\control\startup_flow.json" >nul 2>&1
if exist "%LOCALAPPDATA%\LRAutomatic\control\agent_open_catalog.txt" del /q "%LOCALAPPDATA%\LRAutomatic\control\agent_open_catalog.txt" >nul 2>&1

set /p REMOVEDATA=Deseja apagar tambem fila, logs e configuracoes locais em %%LOCALAPPDATA%%\LRAutomatic? [S/N]: 
if /I "%REMOVEDATA%"=="S" (
  rmdir /s /q "%LOCALAPPDATA%\LRAutomatic" >nul 2>&1
  echo Dados locais removidos.
) else (
  echo Dados locais preservados para diagnostico ou reinstalacao.
)

echo.
echo ================================================
echo  LRAutomatic removido com sucesso
echo  Fotos e catalogos foram preservados.
echo.
echo  O plugin do Lightroom e removido separadamente em:
echo  Arquivo ^> Gerenciador de plug-ins ^> Remover
echo ================================================
pause
exit /b 0
