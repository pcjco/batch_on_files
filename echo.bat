@echo off
@echo arg=%*
FOR /F %%n IN ('powershell -NoLogo -NoProfile -Command Get-Random -Maximum 10') DO (SET "RND=%%~n")
ping 127.0.0.1 -n %RND% > nul
EXIT 0