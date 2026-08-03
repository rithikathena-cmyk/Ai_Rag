from enum import StrEnum
from pathlib import Path


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    HTML = "html"
    IMAGE = "image"
    MARKDOWN = "markdown"
    TXT = "txt"
    XLSX = "xlsx"
    CSV = "csv"
    JSON = "json"
    XML = "xml"
    SQL = "sql"
    CODE = "code"


EXTENSION_MAP: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".pptx": DocumentFormat.PPTX,
    ".html": DocumentFormat.HTML,
    ".htm": DocumentFormat.HTML,
    ".png": DocumentFormat.IMAGE,
    ".jpg": DocumentFormat.IMAGE,
    ".jpeg": DocumentFormat.IMAGE,
    ".tif": DocumentFormat.IMAGE,
    ".tiff": DocumentFormat.IMAGE,
    ".bmp": DocumentFormat.IMAGE,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".txt": DocumentFormat.TXT,
    ".xlsx": DocumentFormat.XLSX,
    ".xls": DocumentFormat.XLSX,
    ".csv": DocumentFormat.CSV,
    ".json": DocumentFormat.JSON,
    ".xml": DocumentFormat.XML,
    ".sql": DocumentFormat.SQL,
    ".py": DocumentFormat.CODE,
    ".js": DocumentFormat.CODE,
    ".jsx": DocumentFormat.CODE,
    ".ts": DocumentFormat.CODE,
    ".tsx": DocumentFormat.CODE,
    ".java": DocumentFormat.CODE,
    ".go": DocumentFormat.CODE,
    ".rs": DocumentFormat.CODE,
    ".c": DocumentFormat.CODE,
    ".h": DocumentFormat.CODE,
    ".cpp": DocumentFormat.CODE,
    ".cc": DocumentFormat.CODE,
    ".hpp": DocumentFormat.CODE,
    ".rb": DocumentFormat.CODE,
    ".php": DocumentFormat.CODE,
    ".cs": DocumentFormat.CODE,
}


def detect_format(filename: str, content_type: str | None = None) -> DocumentFormat | None:
    ext = Path(filename).suffix.lower()
    return EXTENSION_MAP.get(ext)
