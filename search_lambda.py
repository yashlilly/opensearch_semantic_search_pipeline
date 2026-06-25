"""
search_lambda.py  —  RAG Search Pipeline

Flow:
  1. Receive question
  2. Embed question via Azure OpenAI (text-embedding-3-large)
  3. Hybrid search on OpenSearch (BM25 + vector RRF)
  4. Pass top chunks + question to GPT for direct answer
  5. Return answer + source chunks
"""

import json
import os
import logging
import boto3
import requests

from embeddings import EmbeddingsHelper
from opensearch import OpenSearchHelper

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OPENSEARCH_SECRET_ARN = os.environ["OPENSEARCH_SECRET_ARN"]
OPENAI_SECRET_ARN     = os.environ["OPENAI_SECRET_ARN"]
OPENSEARCH_INDEX      = os.environ.get("OPENSEARCH_INDEX", "demo_index_semantic")
REGION                = os.environ.get("REGION", "us-east-1")
TOP_K                 = int(os.environ.get("TOP_K", "3"))

_os_helper  = None
_emb_helper = None
_oai_secret = None


def _get_secret(arn):
    sm = boto3.client("secretsmanager", region_name=REGION)
    return json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])


def _init():
    global _os_helper, _emb_helper, _oai_secret

    logger.info("Cold start — initialising")

    os_secret   = _get_secret(OPENSEARCH_SECRET_ARN)
    _oai_secret = _get_secret(OPENAI_SECRET_ARN)

    token = EmbeddingsHelper.get_token(
        tenant_id     = _oai_secret["AZURE_TENANT_ID"],
        client_id     = _oai_secret["AZURE_CLIENT_ID"],
        client_secret = _oai_secret["AZURE_CLIENT_SECRET"],
    )

    _emb_helper = EmbeddingsHelper(
        api_base        = _oai_secret["AZURE_OPENAI_ENDPOINT"],
        token           = token,
        api_version     = _oai_secret.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        deployment_name = _oai_secret.get("EMBEDDING_MODEL_DEPLOYMENT_NAME", "text-embedding-3-large"),
    )

    _os_helper = OpenSearchHelper(host=os_secret["HOST"], region=REGION)
    logger.info("Helpers ready")


def _ask_gpt(question, chunks):
    api_base    = _oai_secret["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    api_version = _oai_secret.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
    deployment  = _oai_secret.get("AZURE_OPENAI_LLM_DEPLOYMENT_NAME", "gpt-4")

    endpoint = f"{api_base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    logger.info(f"GPT endpoint: {endpoint}")

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"[Source {i} - {chunk['file_name']}, chunk {chunk['chunk_index']}]\n{chunk['chunk_text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    system_prompt = (
        "You are a helpful assistant for Eli Lilly manufacturing teams. "
        "The source chunks may start with document headers or metadata before the actual content — read through ALL of it carefully. "
        "The answer is almost always present somewhere in the chunks even if not at the very beginning. "
        "Answer the user question using ONLY the provided source chunks. "
        "Be concise and direct. Quote or paraphrase the relevant part. "
        "Only say the answer is not found if you have read every word and it is truly absent. "
        "Do not make up information."
    )

    user_prompt = f"Question: {question}\n\nIMPORTANT: Read ALL of each source chunk carefully from start to finish before answering.\n\nSource chunks:\n{context}\n\nAnswer the question based on the sources above."

    token = EmbeddingsHelper.get_token(
        tenant_id     = _oai_secret["AZURE_TENANT_ID"],
        client_id     = _oai_secret["AZURE_CLIENT_ID"],
        client_secret = _oai_secret["AZURE_CLIENT_SECRET"],
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_completion_tokens": 500,
        "temperature": 0.0,
    }

    resp = requests.post(endpoint, headers=headers, json=body, timeout=60)

    if not resp.ok:
        logger.error(f"GPT error {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()

    answer = resp.json()["choices"][0]["message"]["content"].strip()
    logger.info(f"GPT answered — {len(answer)} chars")
    return answer


def lambda_handler(event, context):
    if _os_helper is None:
        _init()

    question = event.get("question", "").strip()
    if not question:
        return {"statusCode": 400, "answer": "No question provided.", "sources": []}

    logger.info(f"Question: {question}")

    query_vector = _emb_helper.embed_single(question)

    results = _os_helper.hybrid_search(
        index_name   = OPENSEARCH_INDEX,
        query_text   = question,
        query_vector = query_vector,
        top_k        = TOP_K,
    )

    if not results:
        return {"statusCode": 200, "answer": "No relevant documents found.", "sources": []}

    answer = _ask_gpt(question, results)

    sources = [
        {
            "file_name":   r["file_name"],
            "chunk_index": r["chunk_index"],
            "score":       r["score"],
            "snippet":     r["chunk_text"][:300],
        }
        for r in results
    ]

    return {
        "statusCode": 200,
        "question":   question,
        "answer":     answer,
        "sources":    sources,
    }