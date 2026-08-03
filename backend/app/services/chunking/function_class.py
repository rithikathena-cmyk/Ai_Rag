import re

from app.services.chunking import recursive, text_utils
from app.services.chunking.types import Chunk

_TIER1_LANGUAGES = {
    ".py": ("tree_sitter_python", "language", None,
            "(function_definition name: (identifier) @func.name) (class_definition name: (identifier) @class.name)"),
    ".js": ("tree_sitter_javascript", "language", None,
            "(function_declaration name: (identifier) @func.name) (class_declaration name: (identifier) @class.name) "
            "(method_definition name: (property_identifier) @method.name)"),
    ".jsx": ("tree_sitter_javascript", "language", None,
             "(function_declaration name: (identifier) @func.name) (class_declaration name: (identifier) @class.name) "
             "(method_definition name: (property_identifier) @method.name)"),
    ".ts": ("tree_sitter_typescript", "language_typescript", None,
            "(function_declaration name: (identifier) @func.name) (class_declaration name: (type_identifier) @class.name) "
            "(method_definition name: (property_identifier) @method.name)"),
    ".tsx": ("tree_sitter_typescript", "language_tsx", None,
             "(function_declaration name: (identifier) @func.name) (class_declaration name: (type_identifier) @class.name) "
             "(method_definition name: (property_identifier) @method.name)"),
    ".c": ("tree_sitter_c", "language", None,
           "(function_definition declarator: (function_declarator declarator: (identifier) @func.name))"),
    ".h": ("tree_sitter_c", "language", None,
           "(function_definition declarator: (function_declarator declarator: (identifier) @func.name))"),
    ".java": ("tree_sitter_java", "language", None,
              "(class_declaration name: (identifier) @class.name) (method_declaration name: (identifier) @method.name)"),
    ".cs": ("tree_sitter_c_sharp", "language", None,
            "(class_declaration name: (identifier) @class.name) (method_declaration name: (identifier) @method.name)"),
}

_TIER2_PATTERNS = [
    re.compile(r"^\s*(func|fn)\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(public|private|protected)?\s*(static\s+)?[\w<>\[\]]+\s+(\w+)\s*\(", re.MULTILINE),
    re.compile(r"^\s*def\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
]


def _tier1_chunks(text: str, ext: str) -> list[Chunk]:
    module_name, lang_fn_name, _unused, query_str = _TIER1_LANGUAGES[ext]
    import importlib
    from tree_sitter import Language, Parser, Query, QueryCursor

    module = importlib.import_module(module_name)
    lang_obj = getattr(module, lang_fn_name)()
    LANG = Language(lang_obj)
    parser = Parser(LANG)
    source_bytes = text.encode("utf-8", errors="replace")
    tree = parser.parse(source_bytes)

    query = Query(LANG, query_str)
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    spans: list[tuple[int, int, str, str]] = []
    for capture_name, nodes in captures.items():
        kind = "class" if "class" in capture_name else "function"
        for node in nodes:
            definition = node.parent
            if definition is None:
                continue
            spans.append((definition.start_byte, definition.end_byte, kind, node.text.decode(errors="replace")))

    spans.sort(key=lambda s: s[0])

    chunks = []
    for i, (start, end, kind, name) in enumerate(spans):
        snippet = source_bytes[start:end].decode("utf-8", errors="replace")
        chunks.append(
            Chunk(index=i, text=snippet, strategy="function_class",
                  token_count=text_utils.count_tokens(snippet), extra={"kind": kind, "name": name})
        )
    return chunks


def _tier2_chunks(text: str) -> list[Chunk]:
    lines = text.splitlines()
    boundaries: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines):
        for pattern in _TIER2_PATTERNS:
            m = pattern.match(line)
            if m:
                name = m.group(m.lastindex) if m.lastindex else ""
                kind = "class" if "class" in pattern.pattern else "function"
                boundaries.append((lineno, kind, name))
                break

    if not boundaries:
        return []

    chunks = []
    for i, (start_line, kind, name) in enumerate(boundaries):
        end_line = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(lines)
        snippet = "\n".join(lines[start_line:end_line]).strip()
        if not snippet:
            continue
        chunks.append(
            Chunk(index=len(chunks), text=snippet, strategy="function_class",
                  token_count=text_utils.count_tokens(snippet), extra={"kind": kind, "name": name})
        )
    return chunks


def chunk(parsed, config, file_extension: str) -> list[Chunk]:
    ext = file_extension.lower()

    if ext in _TIER1_LANGUAGES:
        try:
            chunks = _tier1_chunks(parsed.text, ext)
            if chunks:
                return chunks
        except Exception:
            pass

    chunks = _tier2_chunks(parsed.text)
    if chunks:
        return chunks

    return recursive.chunk(parsed, config, strategy_name="function_class")
