from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NormalizedTable:
    index: int
    headers: list[str]
    rows: list[list[str]]
    caption: str | None = None
    page: int | None = None


@dataclass
class NormalizedImage:
    index: int
    data: bytes
    format: str
    caption: str | None = None
    page: int | None = None


@dataclass
class DocumentMetadata:
    document_type: str
    title: str | None = None
    author: str | None = None
    creation_date: datetime | None = None
    modified_date: datetime | None = None
    language: str | None = None
    page_count: int | None = None
    headings: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass
class NormalizedDocument:
    text: str
    tables: list[NormalizedTable]
    images: list[NormalizedImage]
    metadata: DocumentMetadata


class DocumentParsingError(Exception):
    pass


class UnsupportedFormatError(Exception):
    pass
