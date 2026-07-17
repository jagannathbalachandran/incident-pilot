from abc import ABC, abstractmethod
from typing import Generator, Dict, Any, List

class BaseConnector(ABC):
    """
    Abstract Base Class for connecting to external data repositories 
    (e.g., Confluence, Jira, Slack, local folders) and retrieving raw documents.
    """
    @abstractmethod
    def connect(self) -> None:
        """Establish a connection or session with the source repository."""
        pass

    @abstractmethod
    def fetch_documents(self) -> Generator[Dict[str, Any], None, None]:
        """
        Yields dictionaries representing raw documents with structure:
        {
            "document_id": str,
            "raw_content": str or bytes,
            "title": str,
            "source_type": str,
            "source_url": str,
            "last_updated": str,  # ISO timestamp
            "permissions": list[str]
        }
        """
        pass


class BaseParser(ABC):
    """
    Abstract Base Class for parsing diverse data formats (Markdown, HTML, PDF, DOCX)
    into a unified internal format, chunking the content, and tagging with metadata.
    """
    @abstractmethod
    def parse(self, doc_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses a document's raw content, chunks it, and returns a list 
        of chunks conforming to the Standard Internal Schema.

        Expected output chunks schema:
        {
            "chunk_id": str,       # Unique deterministic hash
            "document_id": str,    # Source document identifier
            "title": str,          # Document title
            "section": str,        # Section title or heading
            "content": str,        # Parsed text or markdown content
            "source_type": str,    # Origin source platform
            "source_url": str,     # Link to source document
            "last_updated": str,   # ISO timestamp
            "permissions": list[str], # List of allowed user/group IDs
            "checksum": str        # SHA-256 hash of the content
        }
        """
        pass


class BaseVectorStore(ABC):
    """
    Abstract Base Class for managing interactions with the Vector Database.
    Handles embedding, database updates, and RBAC-filtered querying.
    """
    @abstractmethod
    def upsert_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """Embeds and upserts chunks into the vector database."""
        pass

    @abstractmethod
    def search(self, query: str, user_permissions: List[str], k: int = 3) -> List[Dict[str, Any]]:
        """
        Performs vector similarity search.
        Restricts results using metadata filtering so that returned chunks are strictly 
        those where the document's 'permissions' match/overlap with user_permissions.
        """
        pass
