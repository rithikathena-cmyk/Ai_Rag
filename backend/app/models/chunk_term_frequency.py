import uuid

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class ChunkTermFrequencyModel(Base):
    __tablename__ = "chunk_term_frequencies"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    term_id: Mapped[int] = mapped_column(Integer, ForeignKey("terms.id", ondelete="CASCADE"), primary_key=True)
    # Raw integer occurrence count, for human-readable visibility/debugging via
    # the /terms and /chunks/{id}/terms endpoints. This intentionally differs
    # from the BM25-saturated float weight stored in the Qdrant sparse vector
    # for the same (chunk, term) pair — see app/services/sparse/service.py.
    term_frequency: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_chunk_term_frequencies_term_id", "term_id"),)
