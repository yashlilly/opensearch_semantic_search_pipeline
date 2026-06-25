"""
lambda_function.py  —  S3 → Chunk → Embed → Hybrid Index pipeline

Trigger:  S3 ObjectCreated (prefix: incoming/)

What makes this different from pure k-NN:
  1. CHUNKING   — each document is split into overlapping 500-word chunks.
                  Each chunk is indexed as a separate document.
                  Better recall for long docs; surfaces exact section not just file.

  2. HYBRID INDEX — the index is built for BOTH BM25 and vector search.
                  'chunk_text' has an English analyser (stemming, stopwords).
                  'embedding' is a knn_vector for semantic search.

  3. IDEMPOTENT — re-uploading the same file first deletes old chunks,
                  then re-indexes fresh ones. No duplicate data.

Supported file types:  .txt  .pdf
Index:                 demo_index_semantic
"""

import json
import os
import io
import logging
import traceback
import urllib.parse
from datetime import datetime, timezone

import boto3

from embeddings import EmbeddingsHelper
from opensearch  import OpenSearchHelper, chunk_text

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment variables ─────────────────────────────────────────────────────
OPENSEARCH_SECRET_ARN = os.environ["OPENSEARCH_SECRET_ARN"]
OPENAI_SECRET_ARN     = os.environ["OPENAI_SECRET_ARN"]
OPENSEARCH_INDEX      = os.environ.get("OPENSEARCH_INDEX",    "demo_index_semantic")
REGION                = os.environ.get("REGION",               "us-east-1")
EMBEDDING_DIMENSION   = int(os.environ.get("EMBEDDING_DIMENSION", "3072"))
CHUNK_WORDS           = int(os.environ.get("CHUNK_WORDS",         "500"))
CHUNK_OVERLAP         = int(os.environ.get("CHUNK_OVERLAP",       "100"))

# ── AWS clients ───────────────────────────────────────────────────────────────
S3 = boto3.client("s3", region_name=REGION)

# ── Module-level cache (warm Lambda invocations reuse these) ──────────────────
_os_helper:  OpenSearchHelper  = None
_emb_helper: EmbeddingsHelper  = None


# ── Secret + helper initialisation ───────────────────────────────────────────

def _get_secret(arn: str) -> dict:
    sm   = boto3.client("secretsmanager", region_name=REGION)
    resp = sm.get_secret_value(SecretId=arn)
    return json.loads(resp["SecretString"])


def _init_helpers():
    global _os_helper, _emb_helper

    logger.info("Cold start — initialising helpers")

    os_secret  = _get_secret(OPENSEARCH_SECRET_ARN)
    oai_secret = _get_secret(OPENAI_SECRET_ARN)

    token = EmbeddingsHelper.get_token(
        tenant_id     = oai_secret["AZURE_TENANT_ID"],
        client_id     = oai_secret["AZURE_CLIENT_ID"],
        client_secret = oai_secret["AZURE_CLIENT_SECRET"],
    )

    _emb_helper = EmbeddingsHelper(
        api_base        = oai_secret["AZURE_OPENAI_ENDPOINT"],
        token           = token,
        api_version     = oai_secret.get("AZURE_OPENAI_API_VERSION", "2023-05-15"),
        deployment_name = oai_secret.get("EMBEDDING_MODEL_DEPLOYMENT_NAME",
                                         "text-embedding-3-large"),
    )
    _os_helper = OpenSearchHelper(host=os_secret["HOST"], region=REGION)
    logger.info("Helpers ready")


def _ensure_index():
    if not _os_helper.index_exists(OPENSEARCH_INDEX):
        logger.info(f"Creating hybrid index '{OPENSEARCH_INDEX}'")
        _os_helper.create_hybrid_index(
            index_name  = OPENSEARCH_INDEX,
            dimension   = EMBEDDING_DIMENSION,
        )
    else:
        logger.info(f"Index '{OPENSEARCH_INDEX}' exists — skipping creation")


# ── Text extraction ───────────────────────────────────────────────────────────

def _read_txt(body_bytes: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return body_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return body_bytes.decode("utf-8", errors="replace")


def _read_pdf(body_bytes: bytes) -> tuple:
    """Returns (full_text, page_count)."""
    try:
        import pypdf
    except ImportError:
        raise RuntimeError("pypdf not installed — add to requirements.txt and redeploy")

    reader     = pypdf.PdfReader(io.BytesIO(body_bytes))
    page_count = len(reader.pages)
    pages      = []

    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:
            logger.warning(f"PDF page {i} extraction failed: {e}")
            pages.append("")

    full_text = "\n\n".join(pages).strip()
    logger.info(f"PDF: {page_count} pages, {len(full_text)} chars extracted")
    return full_text, page_count


# ── S3 move ───────────────────────────────────────────────────────────────────

def _move_s3(bucket: str, src: str, dst: str):
    S3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src}, Key=dst)
    S3.delete_object(Bucket=bucket, Key=src)
    logger.info(f"Moved s3://{bucket}/{src} → {dst}")


# ── Core processing ───────────────────────────────────────────────────────────

def _process_record(record: dict) -> dict:
    bucket    = record["s3"]["bucket"]["name"]
    s3_key    = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    file_size = record["s3"]["object"].get("size", 0)
    file_name = s3_key.split("/")[-1]
    file_ext  = os.path.splitext(file_name)[1].lower()

    logger.info(f"Processing: s3://{bucket}/{s3_key} ({file_ext}, {file_size}B)")

    # ── 1. File type check ────────────────────────────────────────────────────
    if file_ext not in (".txt", ".pdf"):
        logger.warning(f"Unsupported type '{file_ext}' — skipping")
        return {"file": file_name, "status": "skipped",
                "reason": f"Unsupported type: {file_ext}"}

    # ── 2. Read from S3 ───────────────────────────────────────────────────────
    body_bytes = S3.get_object(Bucket=bucket, Key=s3_key)["Body"].read()

    # ── 3. Extract text ───────────────────────────────────────────────────────
    page_count = 0
    if file_ext == ".txt":
        full_text = _read_txt(body_bytes)
    else:
        full_text, page_count = _read_pdf(body_bytes)

    full_text = full_text.strip()
    if not full_text:
        raise ValueError(f"No text extracted from {file_name}")

    total_words = len(full_text.split())
    upload_date = datetime.now(timezone.utc).isoformat()

    logger.info(f"Extracted {total_words} words from {file_name}")

    # ── 4. Delete stale chunks (idempotent re-upload) ─────────────────────────
    _os_helper.delete_chunks_for_file(OPENSEARCH_INDEX, file_name)

    # ── 5. Chunk the document ─────────────────────────────────────────────────
    chunks = chunk_text(full_text, chunk_words=CHUNK_WORDS,
                        overlap_words=CHUNK_OVERLAP)
    total_chunks = len(chunks)
    logger.info(f"Split into {total_chunks} chunks "
                f"(~{CHUNK_WORDS} words each, {CHUNK_OVERLAP} overlap)")

    # ── 6. Embed + build chunk documents ─────────────────────────────────────
    chunk_docs = []
    for chunk_idx, chunk_content in chunks:
        logger.info(f"Embedding chunk {chunk_idx + 1}/{total_chunks}")
        embedding = _emb_helper.embed_single(chunk_content)

        chunk_docs.append({
            "embedding":    embedding,
            "chunk_text":   chunk_content,
            "chunk_index":  chunk_idx,
            "total_chunks": total_chunks,
            "file_name":    file_name,
            "file_type":    file_ext,
            "upload_date":  upload_date,
            "file_size":    file_size,
            "word_count":   len(chunk_content.split()),
            "total_words":  total_words,
            "page_count":   page_count,
            "s3_bucket":    bucket,
            "s3_key":       s3_key,
        })

    # ── 7. Bulk index all chunks ──────────────────────────────────────────────
    indexed = _os_helper.bulk_index_chunks(OPENSEARCH_INDEX, chunk_docs)
    logger.info(f"Indexed {indexed}/{total_chunks} chunks for '{file_name}'")

    # ── 8. Move to processed/ ────────────────────────────────────────────────
    processed_key = s3_key.replace("incoming/", "processed/", 1)
    _move_s3(bucket, s3_key, processed_key)

    return {
        "file":         file_name,
        "status":       "success",
        "chunks":       indexed,
        "total_words":  total_words,
        "page_count":   page_count,
        "index":        OPENSEARCH_INDEX,
    }


# ── Lambda handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    logger.info(f"Invoked — {json.dumps(event)[:400]}")

    if _os_helper is None or _emb_helper is None:
        _init_helpers()

    _ensure_index()

    records = event.get("Records", [])
    if not records:
        logger.warning("No S3 records in event")
        return {"status": "no_records", "results": [], "errors": []}

    results, errors = [], []

    for record in records:
        s3_key = "unknown"
        try:
            s3_key = record.get("s3", {}).get("object", {}).get("key", "unknown")
            result = _process_record(record)
            results.append(result)
            logger.info(f"✅ {result}")

        except Exception as exc:
            logger.error(f"❌ Failed {s3_key}: {exc}")
            logger.error(traceback.format_exc())
            errors.append({"file": s3_key, "error": str(exc)})

            # Move to failed/
            try:
                decoded = urllib.parse.unquote_plus(s3_key)
                bucket  = record["s3"]["bucket"]["name"]
                if decoded.startswith("incoming/"):
                    _move_s3(bucket, decoded,
                             decoded.replace("incoming/", "failed/", 1))
            except Exception as mv_err:
                logger.error(f"Could not move to failed/: {mv_err}")

    status = "success" if not errors else ("partial" if results else "error")

    return {
        "status":    status,
        "processed": len(results),
        "failed":    len(errors),
        "index":     OPENSEARCH_INDEX,
        "results":   results,
        "errors":    errors,
    }
    