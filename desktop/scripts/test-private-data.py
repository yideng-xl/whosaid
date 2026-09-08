"""Synthetic fixtures only: never load the user's vocabulary during tests."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("privacy", Path(__file__).with_name("check-private-data.py"))
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


class PrivacyTests(unittest.TestCase):
    def test_recognizes_renamed_vocabulary(self):
        self.assertTrue(privacy.vocabulary_payload({"version": 2, "libraries": []}))
        self.assertTrue(privacy.vocabulary_payload({"version": 1, "entries": []}))
        self.assertFalse(privacy.vocabulary_payload({"version": "0.2.0", "name": "desktop"}))

    def test_blocks_runtime_files(self):
        for path in ["vocabulary.json", "core/vocabulary.json", "config.json",
                     "recordings/meeting.m4a", "data/.incomplete/id/system.caf",
                     ".env.local", "x.transcript.json"]:
            with self.subTest(path=path):
                self.assertTrue(privacy.private_path(path))

    def test_allows_code_and_synthetic_tests(self):
        for path in ["core/transcribe_core/vocabulary.py", "core/tests/test_vocabulary.py",
                     "desktop/src/lib/VocabularyPanel.svelte"]:
            self.assertFalse(privacy.private_path(path))


if __name__ == "__main__":
    unittest.main()
