"""
Semantic code chunker.

Strategy:
  - Each function or class (with its docstring) → one chunk.
  - If a single function/class exceeds MAX_CHUNK_CHARS, split it but keep
    the function signature in every sub-chunk for context.
  - Module-level code (imports, top-level constants/expressions that aren't
    part of any function/class) → one chunk per file.
  - Every chunk carries: text, file_path, language, start_line, end_line,
    symbol_name, symbol_type.

Result objects are plain dataclasses — no DB imports here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.worker.parser import ParsedFile, ParsedSymbol

logger = logging.getLogger(__name__)

# Approximate character limit for a single chunk (embedding model context)
# BGE-small-en-v1.5 handles ~512 tokens ≈ ~2000 chars comfortably.
# We allow up to 6000 chars and split larger symbols.
MAX_CHUNK_CHARS = 6000
# When splitting large symbols, each sub-chunk is this many lines
SPLIT_CHUNK_LINES = 60

SymbolKind = Literal["function", "class", "module"]


@dataclass
class CodeChunkData:
    """A semantic chunk ready to be embedded and stored."""
    content: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    symbol_name: str | None   # None for module-level chunks
    symbol_type: SymbolKind


def chunk_parsed_file(parsed_file: ParsedFile) -> list[CodeChunkData]:
    """
    Convert a ParsedFile into a list of CodeChunkData objects.

    Returns:
        List of chunks (may be empty if the file has no meaningful content).
    """
    chunks: list[CodeChunkData] = []

    # 1. Symbol chunks (functions and classes)
    for symbol in parsed_file.symbols:
        symbol_chunks = _chunk_symbol(symbol, parsed_file.path, parsed_file.language)
        chunks.extend(symbol_chunks)

    # 2. Imports chunk (all import lines combined into one chunk)
    if parsed_file.imports_content and parsed_file.imports_content.strip():
        chunks.append(CodeChunkData(
            content=f"# File: {parsed_file.path}\n# Imports\n{parsed_file.imports_content}",
            file_path=parsed_file.path,
            language=parsed_file.language,
            start_line=parsed_file.imports_start_line,
            end_line=parsed_file.imports_end_line,
            symbol_name=None,
            symbol_type="module",
        ))

    # 3. Module-level non-import, non-symbol code
    if parsed_file.module_level_content and parsed_file.module_level_content.strip():
        mod_chunks = _split_text_into_chunks(
            text=parsed_file.module_level_content,
            file_path=parsed_file.path,
            language=parsed_file.language,
            start_line=parsed_file.module_level_start_line,
            end_line=parsed_file.module_level_end_line,
            symbol_name=None,
            symbol_type="module",
        )
        chunks.extend(mod_chunks)

    if not chunks:
        logger.debug(
            "No chunks produced for file",
            extra={"path": parsed_file.path},
        )

    return chunks


def _chunk_symbol(
    symbol: ParsedSymbol,
    file_path: str,
    language: str,
) -> list[CodeChunkData]:
    """
    Produce one or more chunks for a function or class symbol.
    If the symbol content fits within MAX_CHUNK_CHARS, it becomes one chunk.
    Otherwise, it is split into sub-chunks, each prefixed with the signature.
    """
    kind: SymbolKind = "class" if symbol.kind == "class" else "function"

    # Prepend file path header for retrieval context
    header = f"# File: {file_path}\n"
    full_content = header + symbol.content

    if len(full_content) <= MAX_CHUNK_CHARS:
        return [CodeChunkData(
            content=full_content,
            file_path=file_path,
            language=language,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            symbol_name=symbol.name,
            symbol_type=kind,
        )]

    # Symbol is large — split by lines, keeping signature in each sub-chunk
    return _split_text_into_chunks(
        text=symbol.content,
        file_path=file_path,
        language=language,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        symbol_name=symbol.name,
        symbol_type=kind,
        signature=_extract_signature(symbol.content),
    )


def _extract_signature(content: str) -> str:
    """
    Extract the function/class signature (first non-empty lines up to the colon, brace, or semicolon).
    Used to prefix sub-chunks of large symbols for context.
    """
    lines = content.splitlines()
    sig_lines: list[str] = []
    for line in lines[:10]:  # signature is always in the first few lines
        sig_lines.append(line)
        stripped = line.rstrip()
        # Stop at the opening colon (Python), brace (C-family, JS, Rust, Go), or semicolon (headers)
        if stripped.endswith(":") or stripped.endswith("{") or stripped.endswith(";"):
            break
    return "\n".join(sig_lines)


def _split_text_into_chunks(
    text: str,
    file_path: str,
    language: str,
    start_line: int,
    end_line: int,
    symbol_name: str | None,
    symbol_type: SymbolKind,
    signature: str | None = None,
) -> list[CodeChunkData]:
    """
    Split a text block into chunks of at most SPLIT_CHUNK_LINES lines each.
    Each chunk is annotated with accurate line ranges.
    """
    lines = text.splitlines()
    total_lines = len(lines)
    chunks: list[CodeChunkData] = []

    prefix = f"# File: {file_path}\n"
    if signature:
        prefix += f"# (Continuation of: {signature})\n"

    for chunk_index, line_offset in enumerate(range(0, total_lines, SPLIT_CHUNK_LINES)):
        chunk_lines = lines[line_offset : line_offset + SPLIT_CHUNK_LINES]
        chunk_text = prefix + "\n".join(chunk_lines)

        # Calculate accurate 1-indexed line range for this sub-chunk
        chunk_start = start_line + line_offset
        chunk_end = min(start_line + line_offset + len(chunk_lines) - 1, end_line)

        chunks.append(CodeChunkData(
            content=chunk_text,
            file_path=file_path,
            language=language,
            start_line=chunk_start,
            end_line=chunk_end,
            symbol_name=symbol_name,
            symbol_type=symbol_type,
        ))

    return chunks
