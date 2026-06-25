@echo off
REM ============================================================
REM  deploy_search.cmd  —  Deploy the Search (RAG) Lambda
REM  Run from s3-semantic-hybrid\ folder in VS Code CMD terminal
REM  Requires .env file with JFROG_TOKEN, JFROG_USER, AWS_ACCOUNT_ID
REM ============================================================

REM Load .env file
for /f "tokens=1,2 delims==" %%a in (.env) do set %%a=%%b

SET SEARCH_FUNCTION_NAME=semantic-search
SET REGION=us-east-1
SET LAYERS_BUCKET=dev-mq-ai-monitoring-lambda-layers
SET ZIP_NAME=semantic-search.zip
SET ROLE_ARN=arn:aws:iam::%AWS_ACCOUNT_ID%:role/dev-mq-ai-monitoring-lambda-role

SET OPENSEARCH_SECRET_ARN=arn:aws:secretsmanager:us-east-1:%AWS_ACCOUNT_ID%:secret:dev-mq-ai-monitoring-opensearch-cluster-details
SET OPENAI_SECRET_ARN=arn:aws:secretsmanager:us-east-1:%AWS_ACCOUNT_ID%:secret:dev-mq-ai-monitoring-openai-secret-key
SET OPENSEARCH_INDEX=demo_index_semantic
SET TOP_K=5

echo.
echo ============================================================
echo  STEP 1: Clean previous build
echo ============================================================
if exist search_package rmdir /s /q search_package
if exist %ZIP_NAME% del %ZIP_NAME%
echo Done.

echo.
echo ============================================================
echo  STEP 2: Install dependencies (JFrog)
echo ============================================================
pip install opensearch-py==2.8.0 requests==2.32.4 --target search_package/ --index-url "https://%JFROG_USER%:%JFROG_TOKEN%@elilillyco.jfrog.io/artifactory/api/pypi/pypi/simple/" --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check .env has correct JFROG_TOKEN and JFROG_USER.
    exit /b 1
)
echo Done.

echo.
echo ============================================================
echo  STEP 3: Copy source files
echo ============================================================
copy search_lambda.py search_package\lambda_function.py
copy embeddings.py search_package\
copy opensearch.py search_package\
echo Done.

echo.
echo ============================================================
echo  STEP 4: Create zip
echo ============================================================
cd search_package
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
echo  STEP 6: Create OR update the search Lambda
echo ============================================================

aws lambda get-function --function-name %SEARCH_FUNCTION_NAME% --region %REGION% >nul 2>&1
if errorlevel 1 (
    echo Function does not exist -- creating...
    aws lambda create-function ^
        --function-name %SEARCH_FUNCTION_NAME% ^
        --runtime python3.12 ^
        --role %ROLE_ARN% ^
        --handler lambda_function.lambda_handler ^
        --code S3Bucket=%LAYERS_BUCKET%,S3Key=deployments/%ZIP_NAME% ^
        --timeout 60 ^
        --memory-size 256 ^
        --vpc-config SubnetIds=subnet-05baa5a3041b3ee11^,subnet-0a798ada05f7507ad^,subnet-0c77405816a0726e9,SecurityGroupIds=sg-0b2b90b90baabc569 ^
        --environment "Variables={OPENSEARCH_SECRET_ARN=%OPENSEARCH_SECRET_ARN%,OPENAI_SECRET_ARN=%OPENAI_SECRET_ARN%,OPENSEARCH_INDEX=%OPENSEARCH_INDEX%,TOP_K=%TOP_K%,REGION=%REGION%}" ^
        --region %REGION%
) else (
    echo Function exists -- updating...
    aws lambda update-function-code ^
        --function-name %SEARCH_FUNCTION_NAME% ^
        --s3-bucket %LAYERS_BUCKET% ^
        --s3-key deployments/%ZIP_NAME% ^
        --region %REGION%

    aws lambda wait function-updated --function-name %SEARCH_FUNCTION_NAME% --region %REGION%

    aws lambda update-function-configuration ^
        --function-name %SEARCH_FUNCTION_NAME% ^
        --timeout 60 ^
        --memory-size 256 ^
        --environment "Variables={OPENSEARCH_SECRET_ARN=%OPENSEARCH_SECRET_ARN%,OPENAI_SECRET_ARN=%OPENAI_SECRET_ARN%,OPENSEARCH_INDEX=%OPENSEARCH_INDEX%,TOP_K=%TOP_K%,REGION=%REGION%}" ^
        --region %REGION%
)

echo.
echo ============================================================
echo  STEP 7: Wait for function to be ready...
echo ============================================================
aws lambda wait function-updated --function-name %SEARCH_FUNCTION_NAME% --region %REGION%

echo.
echo ============================================================
echo  DEPLOYMENT COMPLETE!
echo ============================================================
echo.
echo  Search function : %SEARCH_FUNCTION_NAME%
echo  Index           : %OPENSEARCH_INDEX%
echo  Top K results   : %TOP_K%
echo.
echo  TEST IT:
echo  test_search.cmd "what is a bent plunger?"
echo.