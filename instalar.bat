@echo off
setlocal EnableExtensions
cd /d "%~dp0"

 echo ==============================================
 echo  LRAutomatic - Instalacao
 echo ==============================================
 echo.

set "PYTHON_CMD="
py -3.12 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD (
  py -3.11 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.11"
)
if not defined PYTHON_CMD (
  py -3 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  python -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERRO] Python 3.11 ou superior nao foi encontrado.
  if not defined LRAUTOMATIC_NO_PAUSE pause
  exit /b 1
)

 echo [OK] Usando: %PYTHON_CMD%
%PYTHON_CMD% --version

rem Se a venv existe mas nao consegue importar o pacote, ela esta quebrada.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import lrautomatic" >nul 2>nul
  if errorlevel 1 (
    echo [AVISO] Ambiente virtual quebrado encontrado. Recriando...
    rmdir /s /q ".venv"
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Criando ambiente virtual...
  %PYTHON_CMD% -m venv ".venv"
  if errorlevel 1 goto :erro_venv
) else (
  echo [1/4] Ambiente virtual valido encontrado.
)

 echo [2/4] Atualizando ferramentas de instalacao...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :erro_pip

 echo [3/4] Instalando LRAutomatic...
".venv\Scripts\python.exe" -m pip uninstall -y lrautomatic >nul 2>nul
".venv\Scripts\python.exe" -m pip install --no-cache-dir --force-reinstall .
if errorlevel 1 goto :erro_install

 echo [4/4] Validando instalacao...
".venv\Scripts\python.exe" -c "import lrautomatic; import lrautomatic.cli; print('[OK] Pacote importado:', lrautomatic.__file__)"
if errorlevel 1 goto :erro_import

if not exist "config.json" copy /Y "config.example.json" "config.json" >nul

 echo.
 echo ==============================================
 echo  INSTALACAO CONCLUIDA COM SUCESSO
 echo ==============================================
 echo Agora execute: abrir_app.bat
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 0

:erro_venv
echo [ERRO] Nao foi possivel criar o ambiente virtual.
goto :fim_erro
:erro_pip
echo [ERRO] Falha ao atualizar pip/setuptools/wheel.
goto :fim_erro
:erro_install
echo [ERRO] Falha ao instalar o projeto.
goto :fim_erro
:erro_import
echo [ERRO] O pacote foi instalado, mas nao pode ser importado.
goto :fim_erro
:fim_erro
echo Gere ou envie o diagnostico/saida completa.
if not defined LRAUTOMATIC_NO_PAUSE pause
exit /b 1
