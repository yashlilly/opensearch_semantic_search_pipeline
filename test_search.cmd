@echo off
REM ============================================================
REM  test_search.cmd  —  Test the semantic search Lambda
REM  Usage: test_search.cmd "your question here"
REM  Example: test_search.cmd "what is a bent plunger?"
REM ============================================================

SET QUESTION=%~1
SET REGION=us-east-1
SET FUNCTION=semantic-search

if "%QUESTION%"=="" (
    echo Usage: test_search.cmd "your question here"
    echo Example: test_search.cmd "what is a bent plunger?"
    exit /b 1
)

echo.
echo Question: %QUESTION%
echo Searching...
echo.

aws lambda invoke ^
    --function-name %FUNCTION% ^
    --payload "{\"question\": \"%QUESTION%\"}" ^
    --cli-binary-format raw-in-base64-out ^
    --region %REGION% ^
    response.json

echo.
echo Answer:
echo -------
type response.json
echo.