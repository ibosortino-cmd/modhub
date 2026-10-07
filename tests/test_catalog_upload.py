import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import catalog_upload


class UploadTests(unittest.TestCase):
    def test_unchanged_files_are_recognised_by_githubs_own_hash(self):
        # `git hash-object` of "hello\n"
        self.assertEqual(catalog_upload.git_blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_the_public_catalog_holds_no_game_files(self):
        root = Path(__file__).resolve().parent.parent / "public-catalog"
        too_big = [p.name for p in root.rglob("*") if p.is_file() and p.stat().st_size > 5 * 1024 * 1024]
        self.assertEqual(too_big, [])  # mods and settings only: the game itself is never published
        self.assertFalse(list(root.rglob("*.exe")))


if __name__ == "__main__":
    unittest.main()
