# backend/search.py
"""Search indexing utilities.

Provides:
- `SearchIndexingFabric` for semantic vector storage in Qdrant.
- `KeywordDocument` (SQLAlchemy model) and helper functions for keyword indexing.
"""

from __future__ import annotations

from typing import List, Dict, Any
import os

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

# ---------------------------------------------------------------------------
# Semantic (vector) engine
# ---------------------------------------------------------------------------

class SearchIndexingFabric:
    """Thin wrapper around Qdrant for collection management and upserts.

    Connects to a locally‑running Qdrant instance (default ``http://localhost:6333``).
    Collections are created with cosine distance and a configurable dimensionality
    (default 384, matching common sentence‑transformer embeddings).
    """

    def __init__(self, host: str = None, port: int = None):
        host = host or os.getenv("QDRANT_HOST", "localhost")
        port = port or int(os.getenv("QDRANT_PORT", "6333"))
        self.client = QdrantClient(host=host, port=port)

    def initialize_collection(self, collection_name: str, dim: int = 384, force: bool = False) -> None:
        """Create or recreate a collection.

        Parameters
        ----------
        collection_name: str
            Name of the collection inside Qdrant.
        dim: int, optional
            Vector dimensionality. Defaults to 384.
        force: bool, optional
            If True, recreates the collection even if it already exists, wiping all vectors.
        """
        if not force:
            try:
                self.client.get_collection(collection_name)
                return
            except Exception:
                pass

        self.client.recreate_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    def index_document(
        self,
        collection_name: str,
        doc_id: int,
        vector: List[float],
        payload: Dict[str, Any] | None = None,
    ) -> None:
        """Upsert a single point into the specified collection.

        Parameters
        ----------
        collection_name: str
            Target Qdrant collection.
        doc_id: int
            Unique identifier for the document.
        vector: List[float]
            Embedding vector (must match collection dimensionality).
        payload: dict, optional
            Arbitrary metadata stored alongside the vector.
        """
        point = PointStruct(id=doc_id, vector=vector, payload=payload or {})
        self.client.upsert(collection_name=collection_name, points=[point])

# ---------------------------------------------------------------------------
# Keyword (full‑text) engine – lightweight SQLAlchemy implementation
# ---------------------------------------------------------------------------

from sqlalchemy import Column, Integer, Text, create_engine
from sqlalchemy.orm import declarative_base, Session

Base = declarative_base()

_KEYWORD_ENGINES: Dict[str, Any] = {}

class KeywordDocument(Base):
    """Simple table for keyword / full‑text search.

    Uses SQLite by default; can be swapped for PostgreSQL or a Lucene mock.
    """

    __tablename__ = "keyword_documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    source = Column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KeywordDocument id={self.id!r} title={self.title!r} source={self.source!r}>"

def get_keyword_engine(db_url: str = None) -> Session:
    """Create a SQLAlchemy session bound to the lightweight keyword store.

    Uses environment variable KEYWORD_DB_PATH or falls back gracefully to a
    writable local path if ``/app`` does not exist.
    """
    if not db_url:
        env_path = os.getenv("KEYWORD_DB_PATH")
        if env_path:
            db_url = f"sqlite:///{os.path.abspath(env_path)}"
        elif os.path.exists("/app") and os.access("/app", os.W_OK):
            db_url = "sqlite:////app/keyword_store.db"
        else:
            db_url = f"sqlite:///{os.path.abspath('keyword_store.db')}"
    engine = _KEYWORD_ENGINES.get(db_url)
    if engine is None:
        engine = create_engine(db_url, echo=False, future=True)
        Base.metadata.create_all(engine)
        _KEYWORD_ENGINES[db_url] = engine
    return Session(engine)

def add_keyword_document(session: Session, title: str, content: str, source: str = None) -> KeywordDocument:
    """Insert a new document into the keyword store and return the ORM object."""
    doc = KeywordDocument(title=title, content=content, source=source)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc