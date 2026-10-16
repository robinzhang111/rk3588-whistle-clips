@echo off
setlocal
cd /d "%~dp0"
echo BasketClip unified console. Keep this window open. Local exit does not stop board capture.
if exist "outputs\workbench-user\state.json" (
  python -B workbench.py --output "outputs\workbench-user" --resume --open-browser
) else (
  python -B workbench.py --output "outputs\workbench-user" --open-browser
)
echo Local service stopped. Saved files are preserved.
pause
