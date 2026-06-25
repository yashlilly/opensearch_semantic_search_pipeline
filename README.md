# Semantic Search Pipeline — S3 → OpenSearch → GPT

A fully automated document intelligence system built on AWS Lambda, OpenSearch, and Azure OpenAI. Upload any `.txt` or `.pdf` file to S3 and the system automatically chunks it, generates embeddings, and indexes it. Ask natural language questions and get direct GPT-powered answers backed by your documents.

---

## How It Works

```
Upload .txt or .pdf to S3 incoming/
        ↓
S3 triggers Lambda automatically
        ↓
Lambda extracts text, splits into 500-word overlapping chunks
        ↓
Each chunk embedded via Azure OpenAI (text-embedding-3-large, 3072-dim)
        ↓
Chunks stored in OpenSearch hybrid index (BM25 + vector)
        ↓
Ask a question → question embedded → hybrid search (BM25 + kNN via RRF)
        ↓
Top chunks passed to GPT-5.2 → direct answer returned
```

---

## Infrastructure

| Resource | Name |
|---|---|
| S3 Bucket | `yash-opensearch-documents` |
| Indexing Lambda | `s3-to-opensearch` |
| Search Lambda | `semantic-search` |
| OpenSearch Domain | `dev-mq-ai-monitoring-cust-v2` |
| OpenSearch Index | `demo_index_semantic` |
| Embeddings Model | `text-embedding-3-large` (3072-dim) |
| Chat Model | `gpt-5.2` |
| AWS Region | `us-east-1` |
| AWS Account | `` |

---

## Project Structure

```
s3-semantic-hybrid/
├── lambda_function.py    — Indexing Lambda (S3 trigger → chunk → embed → index)
├── search_lambda.py      — Search Lambda (question → hybrid search → GPT answer)
├── embeddings.py         — Azure OpenAI embeddings REST client (no SDK)
├── opensearch.py         — OpenSearch hybrid index, bulk index, RRF search
├── requirements.txt      — Python dependencies
├── deploy.cmd            — Deploy indexing Lambda
├── deploy_search.cmd     — Deploy search Lambda
├── test_search.cmd       — Test search from terminal
├── .env.example          — Environment variable template
└── .gitignore            — Excludes .env, zips, build folders
```

---

## File Descriptions

### `lambda_function.py`
The indexing Lambda triggered by S3. For each uploaded file it:
- Reads the file from S3
- Extracts text (`.txt` decoded directly, `.pdf` via pypdf)
- Deletes existing chunks for the file (idempotent re-uploads)
- Splits text into 500-word overlapping chunks (100-word overlap)
- Embeds each chunk via Azure OpenAI
- Bulk indexes all chunks to OpenSearch
- Moves the file from `incoming/` to `processed/` (or `failed/` on error)

### `search_lambda.py`
The search Lambda invoked manually or via API. For each question it:
- Embeds the question via Azure OpenAI
- Runs hybrid search — BM25 (keyword) and k-NN (vector) in parallel
- Merges results using Reciprocal Rank Fusion (RRF)
- Passes top chunks to GPT-5.2 with the question
- Returns a direct answer with source references

### `embeddings.py`
Handles Azure OpenAI REST API calls for embeddings. Uses raw `requests` — no OpenAI SDK to avoid Lambda dependency issues. Authenticates via Azure AD client credentials grant.

### `opensearch.py`
Handles all OpenSearch operations:
- `create_hybrid_index` — Creates index with English analyser for BM25 and HNSW for k-NN
- `chunk_text` — Splits text into overlapping chunks
- `bulk_index_chunks` — Indexes chunks with deterministic SHA3-256 IDs
- `hybrid_search` — Runs BM25 + k-NN in parallel, merges with RRF, returns top results

---

## Why Hybrid Search

Pure vector search misses exact term matches (product names, acronyms like AMIGO, SEEQ, VDP). Pure BM25 misses semantic meaning. Hybrid search with RRF combines both — exact matches for technical terms and semantic understanding for conceptual questions.

---

## Setup

### Prerequisites
- AWS CLI configured with valid credentials
- Python 3.x and pip installed
- Access to Lilly JFrog Artifactory
- VPC access to OpenSearch domain

### Step 1: Create `.env` file

```
JFROG_TOKEN=your_jfrog_token_here
JFROG_USER=your_email_urlencoded_here
AWS_ACCOUNT_ID=your_aws_account_id_here
```

### Step 2: Deploy indexing Lambda
```cmd
deploy.cmd
```

### Step 3: Deploy search Lambda
```cmd
deploy_search.cmd
```

### Step 4: Fix S3 trigger
In AWS Lambda console → `s3-to-opensearch` → Configuration → Triggers:
- Delete existing trigger
- Add new trigger: S3, bucket `yash-opensearch-documents`, prefix `incoming/`

---

## Usage

### Index a document
Upload any `.txt` or `.pdf` to S3:
```
S3 → yash-opensearch-documents → incoming/ → Upload
```
Lambda triggers automatically. File appears in `processed/` when done (~30-60 seconds).

### Search
```cmd
test_search.cmd "what is a bent plunger?"
test_search.cmd "how does AMIGO detect anomalies?"
test_search.cmd "what is Denodo used for?"
test_search.cmd "how does SEEQ capsule feature work?"
```

Or invoke the Lambda directly:
```cmd
aws lambda invoke ^
    --function-name semantic-search ^
    --payload "{\"question\": \"what is a bent plunger?\"}" ^
    --cli-binary-format raw-in-base64-out ^
    response.json ^
    --region us-east-1

type response.json
```

### Response format
```json
{
  "statusCode": 200,
  "question": "what is a bent plunger?",
  "answer": "A bent plunger is a plastic plunger rod inside a pharmaceutical injection device that has become mechanically deformed during manufacturing...",
  "sources": [
    {
      "file_name": "amigo-bent-plunger.txt",
      "chunk_index": 0,
      "score": 0.032787,
      "snippet": "..."
    }
  ]
}
```

---

## Lambda Environment Variables

### Indexing Lambda (`s3-to-opensearch`)

| Variable | Value |
|---|---|
| `OPENSEARCH_SECRET_ARN` | ARN of OpenSearch secret |
| `OPENAI_SECRET_ARN` | ARN of Azure OpenAI secret |
| `OPENSEARCH_INDEX` | `demo_index_semantic` |
| `EMBEDDING_DIMENSION` | `3072` |
| `CHUNK_WORDS` | `500` |
| `CHUNK_OVERLAP` | `100` |
| `REGION` | `us-east-1` |

### Search Lambda (`semantic-search`)

| Variable | Value |
|---|---|
| `OPENSEARCH_SECRET_ARN` | ARN of OpenSearch secret |
| `OPENAI_SECRET_ARN` | ARN of Azure OpenAI secret |
| `OPENSEARCH_INDEX` | `demo_index_semantic` |
| `TOP_K` | `5` |
| `REGION` | `us-east-1` |

---


## S3 Folder Structure

```
yash-opensearch-documents/
├── incoming/     ← upload files here
├── processed/    ← files moved here after successful indexing
└── failed/       ← files moved here if indexing fails
```

---

## OpenSearch Index Schema

One document per chunk (not per file):

| Field | Type | Description |
|---|---|---|
| `embedding` | `knn_vector` | 3072-dim vector |
| `chunk_text` | `text` | Chunk content (English analyser) |
| `chunk_index` | `integer` | Position within document |
| `total_chunks` | `integer` | Total chunks in document |
| `file_name` | `keyword` | Original filename |
| `file_type` | `keyword` | `.txt` or `.pdf` |
| `upload_date` | `keyword` | ISO-8601 timestamp |
| `file_size` | `integer` | File size in bytes |
| `word_count` | `integer` | Words in this chunk |
| `total_words` | `integer` | Words in full document |
| `page_count` | `integer` | PDF pages (0 for .txt) |
| `s3_bucket` | `keyword` | Source S3 bucket |
| `s3_key` | `keyword` | Source S3 key |

---


## Dependencies

```
opensearch-py==2.8.0
requests==2.32.4
pypdf==4.3.1
```

Installed via Lilly JFrog Artifactory.