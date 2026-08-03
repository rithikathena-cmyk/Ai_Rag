from pathlib import Path

import pandas as pd

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument, NormalizedTable


def _df_to_table(index: int, df: pd.DataFrame, caption: str | None = None) -> NormalizedTable:
    df = df.fillna("")
    return NormalizedTable(
        index=index,
        headers=[str(c) for c in df.columns],
        rows=df.astype(str).values.tolist(),
        caption=caption,
    )


def parse(file_path: Path, fmt: DocumentFormat) -> NormalizedDocument:
    tables: list[NormalizedTable] = []
    text_parts: list[str] = []

    if fmt == DocumentFormat.CSV:
        df = pd.read_csv(file_path)
        tables.append(_df_to_table(0, df))
        text_parts.append(df.to_markdown(index=False))
    else:
        sheets = pd.read_excel(file_path, sheet_name=None)
        for i, (sheet_name, df) in enumerate(sheets.items()):
            tables.append(_df_to_table(i, df, caption=sheet_name))
            text_parts.append(f"## {sheet_name}\n\n{df.to_markdown(index=False)}")

    return NormalizedDocument(
        text="\n\n".join(text_parts),
        tables=tables,
        images=[],
        metadata=DocumentMetadata(
            document_type=fmt.value,
            title=file_path.stem,
        ),
    )
