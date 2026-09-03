@echo off
set HTTP_PROXY=http://192.168.8.11:7890
set HTTPS_PROXY=http://192.168.8.11:7890
set ALL_PROXY=http://192.168.8.11:7890
set NO_PROXY=localhost,127.0.0.1,192.168.8.11
"%~dp0pi.exe" %*