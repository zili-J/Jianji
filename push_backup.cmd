@echo off
setlocal
cd /d "%~dp0"

rem Switch the console to UTF-8, otherwise the Chinese commit subjects below
rem come out as mojibake: git writes UTF-8, but cmd.exe on a Chinese Windows
rem defaults to code page 936 and renders it as garbage.
chcp 65001 >nul

rem ---------------------------------------------------------------------------
rem One-click backup: push this repository to the git remote named "origin".
rem
rem The FIRST run opens a GitHub sign-in window (Git Credential Manager).
rem Sign in once; the credential is then kept in Windows Credential Manager,
rem so later runs push without asking anything again.
rem
rem Why this has to be double-clicked by you: the sign-in window cannot be
rem shown from inside the sandboxed session the assistant runs in, so this
rem one step must happen in your own console -- and double-clicking this file
rem is exactly that.
rem
rem NOTE: keep this file ASCII-only. cmd.exe on a Chinese Windows defaults to
rem code page 936, and non-ASCII text in a .cmd comes out garbled.
rem ---------------------------------------------------------------------------

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] git not found in PATH.
  echo         Install Git for Windows: https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)

set BRANCH=
for /f "delims=" %%b in ('git branch --show-current') do set BRANCH=%%b
if "%BRANCH%"=="" (
  echo [ERROR] Detached HEAD or no current branch. Check out a branch first.
  echo.
  pause
  exit /b 1
)

echo === remote ===
git remote -v
echo.
echo === branch ===
echo %BRANCH%
echo.
echo === commits to push ===
git --no-pager log --oneline -5
echo.

git push -u origin %BRANCH%
if errorlevel 1 (
  echo.
  echo [FAILED] Push did not succeed -- see the git message above.
  echo          Usual causes: repository does not exist, no write permission,
  echo          or the sign-in window was closed / cancelled.
) else (
  echo.
  echo [OK] Pushed. Double-click this file again any time to back up.
)

echo.
pause
