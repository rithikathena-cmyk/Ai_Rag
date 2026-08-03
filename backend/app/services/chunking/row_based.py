from app.services.chunking import text_utils
from app.services.chunking.types import Chunk


def _row_text(headers: list[str], row: list[str]) -> str:
    if headers:
        return " | ".join(f"{h}: {v}" for h, v in zip(headers, row))
    return " | ".join(row)


def chunk(parsed, config, include_schema_chunk: bool = False) -> list[Chunk]:
    chunks: list[Chunk] = []
    index = 0
    batch_size = config.row_chunk_batch_size

    for table in parsed.tables:
        if include_schema_chunk and table.headers:
            schema_text = f"Table {table.caption or table.index}: columns = {', '.join(table.headers)}"
            chunks.append(
                Chunk(index=index, text=schema_text, strategy="row_based", token_count=text_utils.count_tokens(schema_text),
                      extra={"table_index": table.index, "kind": "schema"})
            )
            index += 1

        for batch_start in range(0, len(table.rows), batch_size):
            batch = table.rows[batch_start : batch_start + batch_size]
            text = "\n".join(_row_text(table.headers, row) for row in batch)
            if not text.strip():
                continue
            chunks.append(
                Chunk(
                    index=index,
                    text=text,
                    strategy="row_based",
                    token_count=text_utils.count_tokens(text),
                    extra={"table_index": table.index, "row_range": [batch_start, batch_start + len(batch)]},
                )
            )
            index += 1

    return chunks
