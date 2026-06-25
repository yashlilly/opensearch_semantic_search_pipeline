"""
opensearch_helper.py

Hybrid search pipeline — completely different from pure k-NN.

APPROACH:
  Index:   Each document is split into overlapping CHUNKS (500 words, 100 overlap).
           Each chunk gets its own embedding + BM25 text field.
           This is better than one embedding per file because:
             - Long docs (10k+ words) lose meaning when compressed to one vector
             - Chunking lets you surface the exact SECTION that answers the query
             - Matches how production RAG systems (like AMIGO itself) work

  Search:  Hybrid — runs TWO queries in parallel then merges with RRF:
             1. BM25 full-text search  (exact terms, acronyms, product names)
             2. Neural/semantic search (meaning, concepts, paraphrases)
           RRF (Reciprocal Rank Fusion) combines both ranked lists
           without needing to tune score weights.

  Why RRF over score normalisation?
    Score normalisation requires calibrating BM25 vs cosine ranges.
    RRF only uses rank positions — simpler, more robust, no tuning needed.

Auth: AWS SigV4 via RequestsAWSV4SignerAuth (IAM, not basic auth).
"""

import hashlib
import logging
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, RequestsAWSV4SignerAuth
from opensearchpy import helpers

logger = logging.getLogger(__name__)

# Chunk settings — tunable via env vars in lambda_function.py
DEFAULT_CHUNK_WORDS   = 500   # words per chunk
DEFAULT_CHUNK_OVERLAP = 100   # words of overlap between adjacent chunks


def chunk_text(text: str, chunk_words: int = DEFAULT_CHUNK_WORDS,
               overlap_words: int = DEFAULT_CHUNK_OVERLAP) -> list:
    """
    Split text into overlapping word-based chunks.

    Example with chunk_words=500, overlap=100:
      chunk 0: words   0 – 499
      chunk 1: words 400 – 899   (100-word overlap with chunk 0)
      chunk 2: words 800 – 1299
      ...

    Returns list of (chunk_index, chunk_text) tuples.
    """
    words = text.split()
    if not words:
        return []

    step   = chunk_words - overlap_words   # advance by 400 words each time
    chunks = []
    start  = 0
    idx    = 0

    while start < len(words):
        end        = min(start + chunk_words, len(words))
        chunk_text = " ".join(words[start:end])
        chunks.append((idx, chunk_text))
        idx   += 1
        start += step

        # If we've covered everything, stop
        if end == len(words):
            break

    logger.info(f"Chunked {len(words)} words → {len(chunks)} chunks "
                f"(size={chunk_words}, overlap={overlap_words})")
    return chunks


class OpenSearchHelper:

    def __init__(self, host: str, region: str = "us-east-1", port: int = 443):
        credentials = boto3.Session().get_credentials()
        auth = RequestsAWSV4SignerAuth(credentials, region, "es")

        self.client = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )
        logger.info(f"OpenSearch client ready — host: {host}")

    # ── Index lifecycle ────────────────────────────────────────────────────────

    def index_exists(self, index_name: str) -> bool:
        return self.client.indices.exists(index=index_name)

    def create_hybrid_index(
        self,
        index_name: str,
        vector_field: str = "embedding",
        dimension:    int  = 3072,
        ef_construction: int = 256,
        m:            int  = 16,
        ef_search:    int  = 256,
    ) -> None:
        """
        Create an index optimised for hybrid (BM25 + vector) search.

        Key differences from a pure k-NN index:
          - knn.algo_param.ef_search is lower (256 vs 512) — faster,
            acceptable because BM25 covers exact-term recall
          - 'chunk_text' is a 'text' field with english analyser for
            strong BM25 (stemming, stopword removal)
          - 'chunk_index' + 'total_chunks' track position within doc
          - 'file_name', 'file_type', 's3_key' are keywords for filtering

        Document shape (one doc per CHUNK, not per file):
          embedding      knn_vector   — vector for this chunk
          chunk_text     text         — raw text of this chunk (BM25 target)
          chunk_index    integer      — position of chunk within document (0-based)
          total_chunks   integer      — total chunks this document was split into
          file_name      keyword      — original filename
          file_type      keyword      — .txt or .pdf
          upload_date    keyword      — ISO-8601
          file_size      integer      — bytes (whole file)
          word_count     integer      — words in this chunk
          total_words    integer      — words in whole document
          page_count     integer      — PDF pages (0 for .txt)
          s3_bucket      keyword
          s3_key         keyword
        """
        body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": ef_search,
                },
                "analysis": {
                    "analyzer": {
                        "english_analyzer": {
                            "type":      "english",
                            "stopwords": "_english_",
                        }
                    }
                },
            },
            "mappings": {
                "properties": {
                    vector_field: {
                        "type":      "knn_vector",
                        "dimension": dimension,
                        "method": {
                            "name":       "hnsw",
                            "space_type": "cosinesimil",
                            "engine":     "nmslib",
                            "parameters": {
                                "ef_construction": ef_construction,
                                "m":               m,
                            },
                        },
                    },
                    "chunk_text":   {"type": "text", "analyzer": "english_analyzer"},
                    "chunk_index":  {"type": "integer"},
                    "total_chunks": {"type": "integer"},
                    "file_name":    {"type": "keyword"},
                    "file_type":    {"type": "keyword"},
                    "upload_date":  {"type": "keyword"},
                    "file_size":    {"type": "integer"},
                    "word_count":   {"type": "integer"},
                    "total_words":  {"type": "integer"},
                    "page_count":   {"type": "integer"},
                    "s3_bucket":    {"type": "keyword"},
                    "s3_key":       {"type": "keyword"},
                },
            },
        }
        self.client.indices.create(index=index_name, body=body)
        logger.info(f"Created hybrid index: {index_name} (dim={dimension})")

    def delete_index(self, index_name: str) -> None:
        self.client.indices.delete(index=index_name)
        logger.info(f"Deleted index: {index_name}")

    # ── Document ID ────────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_id(index_name: str, file_name: str, chunk_index: int) -> str:
        """
        Deterministic ID per chunk — stable across re-runs.
        Re-uploading the same file replaces the same chunks (idempotent).
        """
        key = f"{index_name}|{file_name}|chunk{chunk_index}"
        return hashlib.sha3_256(key.encode()).hexdigest()

    # ── Indexing ───────────────────────────────────────────────────────────────

    def bulk_index_chunks(self, index_name: str, chunk_docs: list) -> int:
        """
        Bulk-index a list of chunk documents.
        Each doc must have 'file_name' and 'chunk_index' fields.
        Returns number successfully indexed.
        """
        actions = [
            {
                "_index": index_name,
                "_id":    self._chunk_id(
                    index_name,
                    doc["file_name"],
                    doc["chunk_index"],
                ),
                "_source": doc,
            }
            for doc in chunk_docs
        ]
        success, failed = helpers.bulk(
            self.client, actions, raise_on_error=False, refresh=True
        )
        if failed:
            reasons = [
                f.get("index", {}).get("error", {}).get("reason", "unknown")
                for f in failed[:3]
            ]
            logger.warning(f"Bulk index — {len(failed)} failed. Reasons: {reasons}")
        logger.info(f"Bulk indexed {success} chunks, {len(failed)} failed")
        return success

    def delete_chunks_for_file(self, index_name: str, file_name: str) -> int:
        """
        Delete all existing chunks for a file before re-indexing.
        Prevents stale chunks if a file is re-uploaded with fewer chunks.
        """
        body = {
            "query": {
                "term": {"file_name": file_name}
            }
        }
        resp = self.client.delete_by_query(index=index_name, body=body, refresh=True)
        deleted = resp.get("deleted", 0)
        if deleted > 0:
            logger.info(f"Deleted {deleted} existing chunks for '{file_name}'")
        return deleted

    # ── Hybrid Search (BM25 + Semantic via RRF) ────────────────────────────────

    def hybrid_search(
        self,
        index_name:    str,
        query_text:    str,
        query_vector:  list,
        vector_field:  str   = "embedding",
        top_k:         int   = 5,
        rrf_rank_const: int  = 60,
        file_filter:   str   = None,
    ) -> list:
        """
        Hybrid search using Reciprocal Rank Fusion (RRF).

        Runs two independent queries:
          1. BM25 on 'chunk_text'   — strong for exact terms, acronyms, names
          2. k-NN on 'embedding'    — strong for meaning, concepts, paraphrases

        Merges with RRF:
          rrf_score(doc) = Σ  1 / (rank_constant + rank_in_list)
          Default rank_constant=60 is the standard value used by Elasticsearch/OpenSearch.

        Deduplicates chunks: if the same chunk scores in both lists,
        its RRF scores are summed (rewarding agreement).

        Also deduplicates by file: returns at most one chunk per document
        (the highest-scoring chunk) to avoid flooding results with chunks
        from the same file.

        Args:
          query_text:    raw query string for BM25
          query_vector:  embedding of the query for k-NN
          top_k:         number of final results to return
          rrf_rank_const: RRF constant (higher = less aggressive reranking)
          file_filter:   optional filename to restrict search to one file

        Returns:
          list of dicts with keys:
            score, file_name, file_type, chunk_index, total_chunks,
            chunk_text, upload_date, total_words
        """
        fetch_size = top_k * 4   # fetch more than needed before dedup

        # Optional file filter
        filter_clause = []
        if file_filter:
            filter_clause = [{"term": {"file_name": file_filter}}]

        # ── BM25 query ────────────────────────────────────────────────────────
        bm25_body = {
            "size": fetch_size,
            "query": {
                "bool": {
                    "must":   [{"match": {"chunk_text": query_text}}],
                    "filter": filter_clause,
                }
            },
            "_source": True,
        }

        # ── k-NN query ────────────────────────────────────────────────────────
        knn_inner = {
            vector_field: {
                "vector": query_vector,
                "k":      fetch_size,
            }
        }
        if filter_clause:
            knn_body = {
                "size": fetch_size,
                "query": {
                    "bool": {
                        "must":   [{"knn": knn_inner}],
                        "filter": filter_clause,
                    }
                },
                "_source": True,
            }
        else:
            knn_body = {
                "size": fetch_size,
                "query": {"knn": knn_inner},
                "_source": True,
            }

        # ── Execute both queries ──────────────────────────────────────────────
        bm25_resp = self.client.search(index=index_name, body=bm25_body)
        knn_resp  = self.client.search(index=index_name, body=knn_body)

        bm25_hits = bm25_resp.get("hits", {}).get("hits", [])
        knn_hits  = knn_resp.get("hits",  {}).get("hits", [])

        logger.info(
            f"Hybrid search — BM25: {len(bm25_hits)} hits, "
            f"kNN: {len(knn_hits)} hits, query: '{query_text[:60]}'"
        )

        # ── RRF fusion ────────────────────────────────────────────────────────
        rrf_scores: dict = {}   # doc_id → rrf_score
        sources:    dict = {}   # doc_id → _source

        for rank, hit in enumerate(bm25_hits, start=1):
            doc_id = hit["_id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (
                1.0 / (rrf_rank_const + rank)
            )
            sources[doc_id] = hit["_source"]

        for rank, hit in enumerate(knn_hits, start=1):
            doc_id = hit["_id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (
                1.0 / (rrf_rank_const + rank)
            )
            if doc_id not in sources:
                sources[doc_id] = hit["_source"]

        # Sort by RRF score descending
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        # ── Deduplicate by file (keep best 2 chunks per file) ───────────────────
        file_counts: dict = {}
        results:    list = []

        for doc_id, score in ranked:
            src       = sources[doc_id]
            file_name = src.get("file_name", "")

            count = file_counts.get(file_name, 0)
            if count >= 2:
                continue
            file_counts[file_name] = count + 1

            results.append({
                "score":        round(score, 6),
                "file_name":    file_name,
                "file_type":    src.get("file_type", ""),
                "chunk_index":  src.get("chunk_index", 0),
                "total_chunks": src.get("total_chunks", 1),
                "chunk_text":   src.get("chunk_text", ""),   # full text for GPT
                "upload_date":  src.get("upload_date", ""),
                "total_words":  src.get("total_words", 0),
            })

            if len(results) >= top_k:
                break

        logger.info(
            f"RRF fusion complete — {len(results)} results "
            f"from {len(rrf_scores)} unique chunks"
        )
        return results

    # ── Stats ──────────────────────────────────────────────────────────────────

    def get_index_stats(self, index_name: str) -> dict:
        """Return doc count and size info for the index."""
        try:
            count_resp = self.client.count(index=index_name)
            stats_resp = self.client.indices.stats(index=index_name)
            doc_count  = count_resp.get("count", 0)
            size_bytes = (
                stats_resp.get("indices", {})
                .get(index_name, {})
                .get("total", {})
                .get("store", {})
                .get("size_in_bytes", 0)
            )
            return {
                "doc_count":  doc_count,
                "size_bytes": size_bytes,
                "size_mb":    round(size_bytes / 1_048_576, 2),
            }
        except Exception as e:
            logger.warning(f"Could not get index stats: {e}")
            return {}