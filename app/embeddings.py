import hashlib
import math
import re
from functools import lru_cache

from app.config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL


TOKEN_PATTERN = re.compile(r"[a-zA-ZÀ-ÿ0-9]{2,}")


def _normalize_text(text: str) -> str:
    return text.strip().lower()


def _hash_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    tokens = TOKEN_PATTERN.findall(_normalize_text(text))
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


@lru_cache(maxsize=1)
def _openai_client():
    try:
        from openai import OpenAI

        return OpenAI()
    except Exception:
        return None


def embed_texts(texts: list[str]) -> list[list[float]]:
    api_client = _openai_client()
    if api_client:
        try:
            response = api_client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
            vectors = [list(item.embedding) for item in response.data]
            if len(vectors) == len(texts):
                return vectors
        except Exception:
            pass

    return [_hash_embedding(text) for text in texts]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0

    total = 0.0
    for left_value, right_value in zip(left, right):
        total += left_value * right_value
    return total