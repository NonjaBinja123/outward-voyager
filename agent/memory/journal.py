"""
ChromaDB-backed adventure journal. Each entry is an event the agent experienced,
stored with an embedding so we can do semantic recall ("what happened near Monsoon?").
"""
import time
from dataclasses import dataclass

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False


@dataclass
class JournalEntry:
    text: str
    scene: str
    tags: list[str]
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


class AdventureJournal:
    def __init__(self, chroma_path: str, collection: str) -> None:
        if not _CHROMA_AVAILABLE:
            self._client = None
            self._col = None
            return
        self._client = chromadb.PersistentClient(path=chroma_path)
        self._col = self._client.get_or_create_collection(collection)

    def record(self, entry: JournalEntry) -> None:
        if self._col is None:
            return
        entry_id = f"{entry.scene}_{int(entry.timestamp * 1000)}"
        self._col.add(
            documents=[entry.text],
            metadatas=[{"scene": entry.scene, "tags": ",".join(entry.tags), "ts": entry.timestamp}],
            ids=[entry_id],
        )

    def recall(self, query: str, n: int = 5, scene: str | None = None) -> list[str]:
        """Semantic search over journal entries."""
        if self._col is None:
            return []
        where = {"scene": scene} if scene else None
        results = self._col.query(
            query_texts=[query],
            n_results=n,
            where=where,
        )
        return results["documents"][0] if results["documents"] else []

    def recent(self, n: int = 10) -> list[str]:
        if self._col is None:
            return []
        all_items = self._col.get(include=["documents", "metadatas"])
        docs = list(zip(all_items["documents"], all_items["metadatas"]))
        docs.sort(key=lambda x: x[1].get("ts", 0), reverse=True)
        return [d for d, _ in docs[:n]]
