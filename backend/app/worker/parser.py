"""
Tree-sitter AST parser for Python, JavaScript, TypeScript, and TSX.

Extracts:
  - Top-level and nested functions with their start/end line numbers
  - Classes with their start/end line numbers
  - Leading docstrings/comments attached to functions and classes
  - Import statements

Only .py, .js, .jsx, .ts, .tsx files are supported.
All other files are silently skipped (caller receives a skip count).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Language = Literal[
    "python", "javascript", "typescript", "tsx", "go", "rust", "java",
    "c", "cpp", "csharp", "ruby", "php", "swift", "kotlin", "html", "css"
]

# Extension → language mapping
EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
}

# Maximum file size to parse (5MB — larger files are chunked as plain text)
MAX_PARSE_BYTES = 5 * 1024 * 1024


@dataclass
class ParsedSymbol:
    """A function or class extracted from source code."""
    name: str
    kind: Literal["function", "class"]
    start_line: int   # 1-indexed
    end_line: int     # 1-indexed
    content: str      # The full source text of this symbol (including docstring)
    docstring: str | None = None


@dataclass
class ParsedFile:
    """Result of parsing a single source file."""
    path: str            # Relative path from repo root
    language: Language
    size_bytes: int
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports_content: str | None = None    # All import lines combined
    imports_start_line: int = 1
    imports_end_line: int = 1
    module_level_content: str | None = None   # Non-import, non-symbol top-level code
    module_level_start_line: int = 1
    module_level_end_line: int = 1


# ── Parser cache (per-thread — tree-sitter Parser is not thread-safe) ────────

import threading
_thread_local = threading.local()


def _get_parser(language: Language):
    """Return a thread-local Tree-sitter parser for the given language.

    Parser objects must not be shared across threads; each thread keeps its own
    cache so concurrent ThreadPoolExecutor calls don't race on the same instance.
    """
    if not hasattr(_thread_local, "parsers"):
        _thread_local.parsers = {}
    if language in _thread_local.parsers:
        return _thread_local.parsers[language]

    try:
        from tree_sitter import Language as TSLanguage, Parser

        if language == "python":
            import tree_sitter_python as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "javascript":
            import tree_sitter_javascript as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "typescript":
            import tree_sitter_typescript as ts_lang
            lang = TSLanguage(ts_lang.language_typescript())
        elif language == "tsx":
            import tree_sitter_typescript as ts_lang
            lang = TSLanguage(ts_lang.language_tsx())
        elif language == "go":
            import tree_sitter_go as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "rust":
            import tree_sitter_rust as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "java":
            import tree_sitter_java as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "c":
            import tree_sitter_c as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "cpp":
            import tree_sitter_cpp as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "csharp":
            import tree_sitter_c_sharp as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "ruby":
            import tree_sitter_ruby as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "php":
            import tree_sitter_php as ts_lang
            lang = TSLanguage(getattr(ts_lang, "language_php", getattr(ts_lang, "language", None))())
        elif language == "swift":
            import tree_sitter_swift as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "kotlin":
            import tree_sitter_kotlin as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "html":
            import tree_sitter_html as ts_lang
            lang = TSLanguage(ts_lang.language())
        elif language == "css":
            import tree_sitter_css as ts_lang
            lang = TSLanguage(ts_lang.language())
        else:
            raise ValueError(f"Unsupported language: {language}")

        parser = Parser(lang)
        _thread_local.parsers[language] = parser
        return parser

    except ImportError as e:
        raise RuntimeError(
            f"Tree-sitter grammar for {language} is not installed. "
            f"Install it with: pip install tree-sitter-{language}\n"
            f"Original error: {e}"
        ) from e


# ── Language-specific node type names ─────────────────────────────────────────

_FUNCTION_TYPES: dict[Language, set[str]] = {
    "python": {"function_definition", "async_function_definition"},
    "javascript": {"function_declaration", "function_expression", "arrow_function",
                   "method_definition", "generator_function_declaration"},
    "typescript": {"function_declaration", "function_expression", "arrow_function",
                   "method_definition", "generator_function_declaration",
                   "abstract_method_signature"},
    "tsx": {"function_declaration", "function_expression", "arrow_function",
            "method_definition", "generator_function_declaration",
            "abstract_method_signature"},
    "go": {"function_declaration", "method_declaration"},
    "rust": {"function_item"},
    "java": {"method_declaration", "constructor_declaration"},
    "c": {"function_definition"},
    "cpp": {"function_definition", "function_declaration"},
    "csharp": {"method_declaration", "constructor_declaration", "local_function_statement"},
    "ruby": {"method", "singleton_method"},
    "php": {"function_definition", "method_declaration"},
    "swift": {"function_declaration", "init_declaration"},
    "kotlin": {"function_declaration", "secondary_constructor"},
    "html": {"script_element", "style_element"},
    "css": {"rule_set"},
}

_CLASS_TYPES: dict[Language, set[str]] = {
    "python": {"class_definition"},
    "javascript": {"class_declaration", "class_expression"},
    "typescript": {"class_declaration", "class_expression", "abstract_class_declaration"},
    "tsx": {"class_declaration", "class_expression", "abstract_class_declaration"},
    "go": {"type_declaration"},  # Go uses interface/struct types
    "rust": {"impl_item", "struct_item", "trait_item", "enum_item"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration"},
    "c": {"struct_specifier", "union_specifier", "enum_specifier"},
    "cpp": {"class_specifier", "struct_specifier", "namespace_definition", "template_declaration"},
    "csharp": {"class_declaration", "interface_declaration", "struct_declaration", "enum_declaration", "record_declaration"},
    "ruby": {"class", "module"},
    "php": {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"},
    "swift": {"class_declaration", "struct_declaration", "protocol_declaration", "extension_declaration", "enum_declaration"},
    "kotlin": {"class_declaration", "object_declaration", "interface_declaration"},
    "html": {"element"},
    "css": {"media_statement", "keyframes_statement"},
}

_IMPORT_TYPES: dict[Language, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement", "import_declaration"},
    "typescript": {"import_statement", "import_declaration"},
    "tsx": {"import_statement", "import_declaration"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
    "c": {"preproc_include"},
    "cpp": {"preproc_include", "using_declaration"},
    "csharp": {"using_directive"},
    "ruby": set(),
    "php": {"namespace_use_declaration"},
    "swift": {"import_declaration"},
    "kotlin": {"import_header"},
    "html": {"doctype"},
    "css": {"import_statement", "at_rule"},
}

_DOCSTRING_TYPES: dict[Language, set[str]] = {
    "python": {"expression_statement"},  # string literal as first statement
    "javascript": set(),
    "typescript": set(),
    "tsx": set(),
    "go": set(),
    "rust": set(),
    "java": set(),
    "c": set(),
    "cpp": set(),
    "csharp": set(),
    "ruby": set(),
    "php": set(),
    "swift": set(),
    "kotlin": set(),
    "html": set(),
    "css": set(),
}

_NAME_FIELDS: dict[str, str] = {
    "function_definition": "name",
    "async_function_definition": "name",
    "function_declaration": "name",
    "generator_function_declaration": "name",
    "class_definition": "name",
    "class_declaration": "name",
    "abstract_class_declaration": "name",
    "method_definition": "name",
    # Go
    "method_declaration": "name",
    # Rust
    "function_item": "name",
    "impl_item": "type",
    "struct_item": "name",
    "trait_item": "name",
    "enum_item": "name",
    # Java
    "constructor_declaration": "name",
    "interface_declaration": "name",
    "enum_declaration": "name",
    # C/C++
    "struct_specifier": "name",
    "union_specifier": "name",
    "class_specifier": "name",
    "namespace_definition": "name",
    "template_declaration": "name",
    # C#
    "local_function_statement": "name",
    "struct_declaration": "name",
    "record_declaration": "name",
    # Ruby
    "method": "name",
    "singleton_method": "name",
    "class": "name",
    "module": "name",
    # PHP
    "trait_declaration": "name",
    # Swift
    "protocol_declaration": "name",
    "extension_declaration": "type",
    # Kotlin
    "object_declaration": "name",
    "secondary_constructor": "name",
}


# ── Main parse function ────────────────────────────────────────────────────────

def detect_language(file_path: Path | str) -> Language | None:
    """Return the language for a file path, or None if unsupported."""
    suffix = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(suffix)


def parse_file(
    file_path: Path,
    repo_root: Path,
    source_bytes: bytes,
) -> ParsedFile | None:
    """
    Parse a source file using Tree-sitter.

    Args:
        file_path: Absolute path to the file.
        repo_root: Absolute path to the repository root (for relative path).
        source_bytes: File content as bytes.

    Returns:
        ParsedFile, or None if the file should be skipped.
    """
    language = detect_language(file_path)
    if language is None:
        return None

    rel_path = str(file_path.relative_to(repo_root))
    size_bytes = len(source_bytes)

    # Decode source
    try:
        source_text = source_bytes.decode("utf-8", errors="replace")
    except Exception:
        logger.warning("Could not decode file", extra={"path": rel_path})
        return None

    source_lines = source_text.splitlines()
    total_lines = len(source_lines)

    # Parse with Tree-sitter
    try:
        parser = _get_parser(language)
        tree = parser.parse(source_bytes)
    except Exception as e:
        logger.warning(
            "Tree-sitter parse failed — falling back to file-level chunk",
            extra={"path": rel_path, "error": str(e)},
        )
        return _fallback_file_chunk(rel_path, language, size_bytes, source_text, total_lines)

    root = tree.root_node

    symbols: list[ParsedSymbol] = []
    import_lines: list[tuple[int, int]] = []   # (start, end) pairs of import nodes

    try:
        # Walk top-level nodes
        _extract_symbols(
            root, source_lines, language, symbols, import_lines, depth=0
        )

        # Build import content
        imports_content = None
        imports_start = 1
        imports_end = 1
        if import_lines:
            imports_start = import_lines[0][0]
            imports_end = import_lines[-1][1]
            import_text_parts = []
            for start, end in import_lines:
                chunk_lines = source_lines[start - 1 : end]
                import_text_parts.append("\n".join(chunk_lines))
            imports_content = "\n".join(import_text_parts)

        # Build module-level non-symbol, non-import content
        module_content, mod_start, mod_end = _extract_module_level(
            root, source_lines, language, import_lines, symbols, total_lines
        )
    except Exception as e:
        logger.warning(
            "AST traversal failed — falling back to file-level chunk",
            extra={"path": rel_path, "error": str(e)},
        )
        return _fallback_file_chunk(rel_path, language, size_bytes, source_text, total_lines)

    return ParsedFile(
        path=rel_path,
        language=language,
        size_bytes=size_bytes,
        symbols=symbols,
        imports_content=imports_content,
        imports_start_line=imports_start,
        imports_end_line=imports_end,
        module_level_content=module_content,
        module_level_start_line=mod_start,
        module_level_end_line=mod_end,
    )


def _extract_symbols(
    node,
    source_lines: list[str],
    language: Language,
    symbols: list[ParsedSymbol],
    import_lines: list[tuple[int, int]],
    depth: int,
) -> None:
    """Recursively extract functions, classes, and imports from AST nodes."""
    fn_types = _FUNCTION_TYPES[language]
    cls_types = _CLASS_TYPES[language]
    imp_types = _IMPORT_TYPES[language]

    for child in node.children:
        ntype = child.type

        if ntype in imp_types:
            # 1-indexed
            start = child.start_point[0] + 1
            end = child.end_point[0] + 1
            import_lines.append((start, end))
            continue

        if ntype in fn_types or ntype in cls_types:
            kind: Literal["function", "class"] = "class" if ntype in cls_types else "function"
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1

            # Include preceding comment block for C-family and others where docstrings are outside
            docstring = None
            if language != "python":
                docstring, new_start = _extract_block_comment_docstring(child, source_lines, start_line)
                start_line = new_start

            # Extract symbol name
            name = _get_symbol_name(child, ntype, language)

            # Extract content
            content_lines = source_lines[start_line - 1 : end_line]
            content = "\n".join(content_lines)

            # Extract docstring for Python
            if language == "python":
                docstring = _extract_python_docstring(child, source_lines)

            symbols.append(ParsedSymbol(
                name=name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                content=content,
                docstring=docstring,
            ))

            # Don't recurse into classes further at top level to avoid
            # double-counting — class methods are included in the class chunk.
            continue

        # Recurse into module-level blocks (e.g., if __name__ == '__main__')
        if depth == 0:
            _extract_symbols(child, source_lines, language, symbols, import_lines, depth + 1)


def _get_symbol_name(node, node_type: str, language: Language) -> str:
    """Extract the identifier name from a function/class AST node."""
    field_name = _NAME_FIELDS.get(node_type, "name")
    name_node = node.child_by_field_name(field_name)
    if name_node:
        return name_node.text.decode("utf-8", errors="replace") if isinstance(name_node.text, bytes) else str(name_node.text)
    # Fallback: anonymous function
    return "<anonymous>"


def _extract_python_docstring(func_node, source_lines: list[str]) -> str | None:
    """
    Extract the docstring from a Python function or class body.
    The docstring is the first statement in the body if it's a string literal.
    """
    body = func_node.child_by_field_name("body")
    if not body:
        return None
    for child in body.children:
        if child.type == "expression_statement":
            for grandchild in child.children:
                if grandchild.type in ("string", "concatenated_string"):
                    start = grandchild.start_point[0] + 1
                    end = grandchild.end_point[0] + 1
                    lines = source_lines[start - 1 : end]
                    return "\n".join(lines).strip()
    return None


def _extract_block_comment_docstring(node, source_lines: list[str], current_start: int) -> tuple[str | None, int]:
    """
    Extract a block comment immediately preceding a node.
    Returns the docstring content and the updated start_line.
    """
    prev = node.prev_sibling
    if prev and prev.type in ("comment", "block_comment", "line_comment"):
        # Only attach if it's immediately preceding (<= 1 blank line)
        if current_start - prev.end_point[0] <= 2:
            new_start = prev.start_point[0] + 1
            docstring = "\n".join(source_lines[new_start - 1 : prev.end_point[0]]).strip()
            return docstring, new_start
    return None, current_start


def _extract_module_level(
    root,
    source_lines: list[str],
    language: Language,
    import_lines: list[tuple[int, int]],
    symbols: list[ParsedSymbol],
    total_lines: int,
) -> tuple[str | None, int, int]:
    """
    Extract module-level code that isn't an import or a function/class.
    Returns (content, start_line, end_line) or (None, 1, 1).
    """
    fn_types = _FUNCTION_TYPES[language]
    cls_types = _CLASS_TYPES[language]
    imp_types = _IMPORT_TYPES[language]

    covered_lines: set[int] = set()
    for start, end in import_lines:
        covered_lines.update(range(start, end + 1))
    for sym in symbols:
        covered_lines.update(range(sym.start_line, sym.end_line + 1))

    module_line_indices = [
        i for i in range(1, total_lines + 1)
        if i not in covered_lines
    ]

    if not module_line_indices:
        return None, 1, 1

    # Only include non-blank, non-comment module lines
    meaningful = [
        i for i in module_line_indices
        if source_lines[i - 1].strip() and not source_lines[i - 1].strip().startswith("#")
    ]

    if not meaningful:
        return None, 1, 1

    # Collect contiguous blocks
    mod_lines = [source_lines[i - 1] for i in meaningful]
    content = "\n".join(mod_lines)
    return content, meaningful[0], meaningful[-1]


def _fallback_file_chunk(
    rel_path: str,
    language: Language,
    size_bytes: int,
    source_text: str,
    total_lines: int,
) -> ParsedFile:
    """Return a ParsedFile with the whole file as a single module chunk."""
    return ParsedFile(
        path=rel_path,
        language=language,
        size_bytes=size_bytes,
        symbols=[],
        module_level_content=source_text,
        module_level_start_line=1,
        module_level_end_line=total_lines,
    )
