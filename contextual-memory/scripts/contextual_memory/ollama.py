from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class OllamaError(RuntimeError):
    pass


@dataclass(slots=True)
class OllamaEmbedder:
    model: str = "embeddinggemma"
    base_url: str = "http://localhost:11434"
    timeout: float = 60.0

    def embed(self, text: str) -> list[float]:
        vectors = self.embed_batch([text])
        return vectors[0] if vectors else []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama at {self.base_url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama returned invalid JSON from /api/embed.") from exc

        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise OllamaError("Ollama /api/embed response did not include the expected embeddings.")
        vectors: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not all(isinstance(item, int | float) for item in vector):
                raise OllamaError("Ollama returned a malformed embedding vector.")
            vectors.append([float(item) for item in vector])
        return vectors
