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
    # Phase 9 — cross-game provenance
    game_id: str = ""              # e.g. "outward_definitive"
    game_display_name: str = ""    # e.g. "Outward Definitive Edition"

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


class AdventureJournal:
    def __init__(self, chroma_path: str, collection: str) -> None:
        self._collection_name = collection
        if not _CHROMA_AVAILABLE:
            self._client = None
            self._col = None
            return
        self._client = chromadb.PersistentClient(path=chroma_path)
        self._col = self._client.get_or_create_collection(collection)

    def _refresh_col(self) -> None:
        """Re-acquire the collection handle — recovers from stale UUID after external deletion."""
        if self._client is not None:
            self._col = self._client.get_or_create_collection(self._collection_name)

    def record(self, entry: JournalEntry) -> None:
        if self._col is None:
            return
        entry_id = f"{entry.scene}_{int(entry.timestamp * 1000)}"
        metadata: dict = {
            "scene": entry.scene,
            "tags": ",".join(entry.tags),
            "ts": entry.timestamp,
        }
        if entry.game_id:
            metadata["game_id"] = entry.game_id
        if entry.game_display_name:
            metadata["game_display_name"] = entry.game_display_name
        try:
            self._col.add(
                documents=[entry.text],
                metadatas=[metadata],
                ids=[entry_id],
            )
        except Exception:
            # Stale collection UUID — re-acquire and retry once
            self._refresh_col()
            if self._col is not None:
                self._col.add(
                    documents=[entry.text],
                    metadatas=[metadata],
                    ids=[entry_id],
                )

    def recall(self, query: str, n: int = 5, scene: str | None = None,
               game_id: str | None = None, exclude_game_id: str | None = None,
               ) -> list[str]:
        """
        Semantic search over journal entries.

        Args:
            query:            Text query for embedding search.
            n:                Max results to return.
            scene:            If set, filter to this scene only.
            game_id:          If set, filter to entries from this game only.
            exclude_game_id:  If set, exclude entries from this game (returns cross-game memories).
        """
        if self._col is None:
            return []

        # Build ChromaDB where clause
        where: dict | None = None
        filters = []
        if scene:
            filters.append({"scene": {"$eq": scene}})
        if game_id:
            filters.append({"game_id": {"$eq": game_id}})
        if exclude_game_id:
            filters.append({"game_id": {"$ne": exclude_game_id}})
        if len(filters) == 1:
            where = filters[0]
        elif len(filters) > 1:
            where = {"$and": filters}

        try:
            results = self._col.query(
                query_texts=[query],
                n_results=n,
                where=where,
            )
            return results["documents"][0] if results["documents"] else []
        except Exception:
            # Either stale UUID or where filter returned no candidates — retry without filter
            self._refresh_col()
            try:
                results = self._col.query(query_texts=[query], n_results=n)
                return results["documents"][0] if results["documents"] else []
            except Exception:
                return []

    def recent(self, n: int = 10) -> list[str]:
        if self._col is None:
            return []
        try:
            all_items = self._col.get(include=["documents", "metadatas"])
        except Exception:
            self._refresh_col()
            try:
                all_items = self._col.get(include=["documents", "metadatas"])
            except Exception:
                return []
        docs = list(zip(all_items["documents"], all_items["metadatas"]))
        docs.sort(key=lambda x: x[1].get("ts", 0), reverse=True)
        return [d for d, _ in docs[:n]]
