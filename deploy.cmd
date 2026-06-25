@echo off
REM ============================================================
REM  deploy.cmd  —  Hybrid Search Indexing Pipeline
REM  Run from this folder in VS Code terminal (CMD, not PowerShell)
REM  Requires .env file with JFROG_TOKEN, JFROG_USER, AWS_ACCOUNT_ID
REM ============================================================

REM Load .env file
for /f "tokens=1,2 delims==" %%a in (.env) do set %%a=%%b

SET FUNCTION_NAME=s3-to-opensearch
SET REGION=us-east-1
SET LAYERS_BUCKET=dev-mq-ai-monitoring-lambda-layers
SET ZIP_NAME=s3-hybrid-embeddings.zip

SET OPENSEARCH_SECRET_ARN=arn:aws:secretsmanager:us-east-1:%AWS_ACCOUNT_ID%:secret:dev-mq-ai-monitoring-opensearch-cluster-details
SET OPENAI_SECRET_ARN=arn:aws:secretsmanager:us-east-1:%AWS_ACCOUNT_ID%:secret:dev-mq-ai-monitoring-openai-secret-key

SET OPENSEARCH_INDEX=demo_index_semantic
SET EMBEDDING_DIMENSION=3072
SET CHUNK_WORDS=500
SET CHUNK_OVERLAP=100

echo.
echo ============================================================
echo  STEP 1: Clean previous build
echo ============================================================
if exist package rmdir /s /q package
if exist %ZIP_NAME% del %ZIP_NAME%
echo Done.

echo.
echo ============================================================
echo  STEP 2: Install dependencies (JFrog)
echo ============================================================
pip install -r requirements.txt --target package/ --index-url "https://%JFROG_USER%:%JFROG_TOKEN%@elilillyco.jfrog.io/artifactory/api/pypi/pypi/simple/" --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check .env has correct JFROG_TOKEN and JFROG_USER.
    exit /b 1
)
echo Done.

echo.
echo ============================================================
echo  STEP 3: Copy Lambda source files
echo ============================================================
copy lambda_function.py package\
copy embeddings.py package\
copy opensearch.py package\
echo Done.

echo.
echo ============================================================
echo  STEP 4: Create deployment zip
echo ============================================================
cd package
powershell -Command "Compress-Archive -Path * -DestinationPath ..\%ZIP_NAME% -Force"
cd ..
echo Done.

echo.
echo ============================================================
echo  STEP 5: Upload zip to S3
echo ============================================================
aws s3 cp %ZIP_NAME% s3://%LAYERS_BUCKET%/deployments/%ZIP_NAME% --region %REGION%
if errorlevel 1 (
    echo ERROR: S3 upload failed. Check AWS credentials.
    exit /b 1
)
echo Done.

echo.
echo ============================================================
echo  STEP 6: Update Lambda function code
echo ============================================================
aws lambda update-function-code ^
    --function-name %FUNCTION_NAME% ^
    --s3-bucket %LAYERS_BUCKET% ^
    --s3-key deployments/%ZIP_NAME% ^
    --region %REGION%
if errorlevel 1 (
    echo ERROR: Lambda code update failed.
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 7: Wait for code update...
echo ============================================================
aws lambda wait function-updated --function-name %FUNCTION_NAME% --region %REGION%
echo Done.

echo.
echo ============================================================
echo  STEP 8: Update Lambda config + env vars
echo ============================================================
aws lambda update-function-configuration ^
    --function-name %FUNCTION_NAME% ^
    --timeout 300 ^
    --memory-size 512 ^
    --environment "Variables={OPENSEARCH_SECRET_ARN=%OPENSEARCH_SECRET_ARN%,OPENAI_SECRET_ARN=%OPENAI_SECRET_ARN%,OPENSEARCH_INDEX=%OPENSEARCH_INDEX%,EMBEDDING_DIMENSION=%EMBEDDING_DIMENSION%,CHUNK_WORDS=%CHUNK_WORDS%,CHUNK_OVERLAP=%CHUNK_OVERLAP%,REGION=%REGION%}" ^
    --region %REGION%
if errorlevel 1 (
    echo ERROR: Lambda config update failed.
    exit /b 1
)

echo.
echo ============================================================
echo  STEP 9: Wait for config update...
echo ============================================================
aws lambda wait function-updated --function-name %FUNCTION_NAME% --region %REGION%

echo.
echo ============================================================
echo  DEPLOYMENT COMPLETE!
echo ============================================================
echo.
echo  Function  : %FUNCTION_NAME%
echo  Index     : %OPENSEARCH_INDEX%
echo  Chunks    : %CHUNK_WORDS% words, %CHUNK_OVERLAP% overlap
echo.
echo  Upload a .txt or .pdf to s3://yash-opensearch-documents/incoming/
echo  Check CloudWatch: /aws/lambda/%FUNCTION_NAME%
echo.