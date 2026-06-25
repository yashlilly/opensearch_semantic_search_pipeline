"""
embeddings_helper.py

Calls Azure OpenAI embeddings REST API directly via requests.
No openai SDK — avoids pydantic v2 / Rust binary issues on Lambda.
Authenticates via Azure AD client credentials grant.
"""

import logging
import requests

logger = logging.getLogger(__name__)


class EmbeddingsHelper:

    def __init__(
        self,
        api_base: str,
        token: str,
        api_version: str = "2023-05-15",
        deployment_name: str = "text-embedding-3-large",
    ):
        self._token = token
        self._endpoint = (
            f"{api_base.rstrip('/')}/openai/deployments/"
            f"{deployment_name}/embeddings?api-version={api_version}"
        )
        self.deployment_name = deployment_name
        logger.info(f"EmbeddingsHelper ready — deployment: {deployment_name}")

    @staticmethod
    def get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
        url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        resp = requests.post(
            url,
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
                "scope":         "https://cognitiveservices.azure.com/.default",
            },
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("Azure AD token acquired")
        return resp.json()["access_token"]

    def _call_api(self, texts: list) -> list:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }
        resp = requests.post(
            self._endpoint,
            headers=headers,
            json={"input": texts},
            timeout=60,
        )
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    def embed_single(self, text: str) -> list:
        text = text.replace("\n", " ").strip()
        if not text:
            raise ValueError("Cannot embed empty text")
        return self._call_api([text])[0]
