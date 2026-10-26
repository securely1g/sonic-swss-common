"""Regression tests for bounded-memory Bazel execution-log parsing."""

import io
import json
from pathlib import Path
import tempfile
from unittest import TestCase, main, mock

import validate


class RecordsTest(TestCase):
    def parse(self, text, chunk_size=17):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.json"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(validate, "LOG_READ_CHUNK_SIZE", chunk_size):
                return list(validate.records(path))

    def test_pretty_printed_adjacent_objects_and_nested_arrays(self):
        expected = [
            {"mnemonic": "CppCompile", "inputs": [{"path": "a.cc"}, {}]},
            {"mnemonic": "CppLink", "outputs": ["libswsscommon.so.0"]},
            {},
        ]
        text = "\n\t" + "".join(json.dumps(value, indent=2) for value in expected) + "\n"
        self.assertEqual(self.parse(text), expected)

    def test_large_record_and_escapes_across_chunk_boundaries(self):
        expected = [
            {"value": "{}\\\"\n\u00e9" * 20000, "nested": {"value": [1, 2]}},
            {"value": "next record"},
        ]
        text = "".join(json.dumps(value, ensure_ascii=False) for value in expected)
        # Single-character chunks split every escaped quote and backslash.
        self.assertEqual(self.parse(text, chunk_size=1), expected)
        self.assertEqual(self.parse(text, chunk_size=validate.LOG_READ_CHUNK_SIZE), expected)

    def test_yields_first_record_without_reading_the_rest_of_the_file(self):
        size = validate.LOG_READ_CHUNK_SIZE
        stream = io.StringIO('{}' + '\n' * size + '{"tail": true}')
        path = mock.Mock()
        path.read_text.side_effect = AssertionError("whole-file read")
        path.open.return_value = stream
        original_read = stream.read
        with mock.patch.object(stream, "read", wraps=original_read) as read:
            iterator = validate.records(path)
            self.assertEqual(next(iterator), {})
            self.assertEqual(stream.tell(), size)
            read.assert_called_once_with(size)
            self.assertEqual(list(iterator), [{"tail": True}])
            self.assertTrue(all(call.args == (size,) for call in read.call_args_list))
        path.read_text.assert_not_called()

    def test_empty_and_whitespace_only_logs(self):
        for text in ("", " \n\t\r" * 30):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), [])

    def test_rejects_truncated_objects_after_a_valid_record(self):
        for tail in ('{', '{"inputs": [', '{"value": "unterminated',
                     '{"value": "escaped\\', '{"nested": {"value": 1}'):
            with self.subTest(tail=tail):
                with self.assertRaises(json.JSONDecodeError):
                    self.parse('{}\n' + tail)

    def test_rejects_malformed_records_and_trailing_garbage(self):
        for text in ('{"value": }', '{"value": 1,}', '{"value": [1}',
                     '{}garbage', '[]', 'null'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.parse(text)


if __name__ == "__main__":
    main()
