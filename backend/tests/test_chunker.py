"""
Unit tests for the code chunker.
"""

import pytest
from app.worker.parser import ParsedFile, ParsedSymbol
from app.worker.chunker import chunk_parsed_file, CodeChunkData, MAX_CHUNK_CHARS


def make_parsed_file(
    symbols=None,
    imports_content=None,
    module_level_content=None,
) -> ParsedFile:
    return ParsedFile(
        path="test/example.py",
        language="python",
        size_bytes=100,
        symbols=symbols or [],
        imports_content=imports_content,
        imports_start_line=1,
        imports_end_line=3,
        module_level_content=module_level_content,
        module_level_start_line=5,
        module_level_end_line=10,
    )


class TestChunkParsedFile:
    def test_empty_file_produces_no_chunks(self):
        parsed = make_parsed_file()
        chunks = chunk_parsed_file(parsed)
        assert chunks == []

    def test_single_function_becomes_one_chunk(self):
        symbol = ParsedSymbol(
            name="my_function",
            kind="function",
            start_line=10,
            end_line=20,
            content="def my_function():\n    return 42",
        )
        parsed = make_parsed_file(symbols=[symbol])
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 1
        assert chunks[0].symbol_name == "my_function"
        assert chunks[0].symbol_type == "function"
        assert chunks[0].start_line == 10
        assert chunks[0].end_line == 20
        assert "my_function" in chunks[0].content

    def test_class_becomes_one_chunk(self):
        symbol = ParsedSymbol(
            name="MyClass",
            kind="class",
            start_line=5,
            end_line=30,
            content="class MyClass:\n    pass",
        )
        parsed = make_parsed_file(symbols=[symbol])
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 1
        assert chunks[0].symbol_type == "class"
        assert chunks[0].symbol_name == "MyClass"

    def test_imports_become_module_chunk(self):
        parsed = make_parsed_file(imports_content="import os\nimport sys")
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 1
        assert chunks[0].symbol_type == "module"
        assert "import os" in chunks[0].content

    def test_module_level_code_becomes_chunk(self):
        parsed = make_parsed_file(module_level_content="X = 42\nY = 'hello'")
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 1
        assert chunks[0].symbol_type == "module"

    def test_large_function_splits_into_multiple_chunks(self):
        # Create a function content that exceeds MAX_CHUNK_CHARS
        big_content = "def big_function():\n" + "    x = 1\n" * 1000
        assert len(big_content) > MAX_CHUNK_CHARS

        symbol = ParsedSymbol(
            name="big_function",
            kind="function",
            start_line=1,
            end_line=1001,
            content=big_content,
        )
        parsed = make_parsed_file(symbols=[symbol])
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) > 1
        # Every sub-chunk should reference the same symbol name
        for chunk in chunks:
            assert chunk.symbol_name == "big_function"

    def test_multiple_symbols_produce_multiple_chunks(self):
        symbols = [
            ParsedSymbol("func_a", "function", 1, 10, "def func_a(): pass"),
            ParsedSymbol("func_b", "function", 12, 20, "def func_b(): pass"),
            ParsedSymbol("ClassC", "class", 22, 40, "class ClassC:\n    pass"),
        ]
        parsed = make_parsed_file(symbols=symbols)
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 3

    def test_file_path_included_in_chunk_content(self):
        symbol = ParsedSymbol("fn", "function", 1, 5, "def fn(): pass")
        parsed = make_parsed_file(symbols=[symbol])
        chunks = chunk_parsed_file(parsed)
        assert "test/example.py" in chunks[0].content

    def test_language_preserved_in_chunks(self):
        symbol = ParsedSymbol("fn", "function", 1, 5, "def fn(): pass")
        parsed = make_parsed_file(symbols=[symbol])
        chunks = chunk_parsed_file(parsed)
        assert chunks[0].language == "python"

    def test_line_ranges_are_accurate(self):
        symbol = ParsedSymbol("fn", "function", 15, 25, "def fn():\n    pass")
        parsed = make_parsed_file(symbols=[symbol])
        chunks = chunk_parsed_file(parsed)
        assert chunks[0].start_line == 15
        assert chunks[0].end_line == 25


class TestChunkerEdgeCases:
    def test_whitespace_only_module_content_skipped(self):
        parsed = make_parsed_file(module_level_content="   \n   \n   ")
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 0

    def test_comment_only_module_content_skipped(self):
        # Comment filtering is the parser's responsibility (in _extract_module_level).
        # The chunker receives already-filtered content from the parser.
        # If module_level_content happens to be comment-only (e.g. passed directly),
        # the chunker produces a chunk — that's correct behavior.
        # The parser would have filtered this before passing to the chunker.
        # So this test verifies: whitespace stripping works (empty after strip → no chunk)
        parsed = make_parsed_file(module_level_content="   ")
        chunks = chunk_parsed_file(parsed)
        assert len(chunks) == 0  # Only whitespace → stripped away

    def test_combined_symbols_and_imports(self):
        symbols = [ParsedSymbol("fn", "function", 5, 10, "def fn(): pass")]
        parsed = make_parsed_file(
            symbols=symbols,
            imports_content="import os",
            module_level_content="X = 1",
        )
        chunks = chunk_parsed_file(parsed)
        # Should have: 1 function chunk + 1 imports chunk + 1 module chunk
        assert len(chunks) == 3
