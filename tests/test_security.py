import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.security import (
    ensure_within_directory,
    sanitize_remote_filename,
    validate_custom_filename,
    validate_output_dir,
)


class TestSecurityValidation(unittest.TestCase):
    def test_validate_custom_filename_accepts_safe_names(self):
        self.assertEqual(validate_custom_filename("  ubuntu-24.04.iso  "), "ubuntu-24.04.iso")
        self.assertEqual(validate_custom_filename("archivo_v1.2.tar.gz"), "archivo_v1.2.tar.gz")

    def test_validate_custom_filename_rejects_traversal_hidden_and_reserved(self):
        bad_names = [
            "",
            "   ",
            ".bashrc",
            "..",
            "../evil.sh",
            r"sub\..\..\evil.sh",
            "/etc/passwd",
            "C:\\Windows\\system32\\cmd.exe",
            "bad\x00name.bin",
            "CON",
            "nul.txt",
            "com1",
            "LPT9.log",
            "bad:name?.iso",
        ]
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_custom_filename(name)

    def test_sanitize_remote_filename_cleans_paths_encoded_traversal_and_reserved(self):
        self.assertEqual(sanitize_remote_filename("../../%2e%2e/etc/passwd"), "passwd")
        self.assertEqual(sanitize_remote_filename(r"folder\..\..\\.bashrc"), "bashrc")
        self.assertEqual(sanitize_remote_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_remote_filename("..."), "download.bin")
        self.assertEqual(sanitize_remote_filename("bad<script>?.iso"), "bad_script__.iso")

    def test_validate_output_dir_policies_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp_home, tempfile.TemporaryDirectory() as outside_dir:
            default_dir = os.path.join(tmp_home, "Downloads", "Aether")
            os.makedirs(default_dir, exist_ok=True)
            safe_sub = os.path.join(tmp_home, "Videos")
            os.makedirs(safe_sub, exist_ok=True)
            hidden_dir = os.path.join(tmp_home, ".ssh")
            os.makedirs(hidden_dir, exist_ok=True)
            nested_hidden = os.path.join(tmp_home, "Downloads", ".hidden")
            os.makedirs(nested_hidden, exist_ok=True)

            symlink_escape = os.path.join(tmp_home, "Downloads", "escape_link")
            os.symlink(outside_dir, symlink_escape)

            with patch("os.path.expanduser", side_effect=lambda p: tmp_home if p == "~" else p.replace("~/", tmp_home + "/", 1)):
                self.assertEqual(validate_output_dir(None, default_dir), os.path.realpath(default_dir))
                self.assertEqual(validate_output_dir("", default_dir), os.path.realpath(default_dir))
                self.assertEqual(validate_output_dir("~/Videos", default_dir), os.path.realpath(safe_sub))

                for forbidden in [tmp_home, "~", hidden_dir, nested_hidden, outside_dir, symlink_escape]:
                    with self.subTest(forbidden=forbidden):
                        with self.assertRaises(ValueError):
                            validate_output_dir(forbidden, default_dir)

    def test_ensure_within_directory_blocks_escapes(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as outside_dir:
            inside = os.path.join(base_dir, "file.bin")
            self.assertEqual(ensure_within_directory(base_dir, inside), os.path.realpath(inside))
            with self.assertRaises(ValueError):
                ensure_within_directory(base_dir, os.path.join(base_dir, "..", "evil.bin"))
            with self.assertRaises(ValueError):
                ensure_within_directory(base_dir, os.path.join(outside_dir, "evil.bin"))

    def test_additional_edge_cases(self):
        self.assertEqual(
            sanitize_remote_filename("%252e%252e%252f%252e%252e%252fetc%252fpasswd"),
            "passwd",
        )
        self.assertEqual(sanitize_remote_filename("///"), "download.bin")
        self.assertEqual(sanitize_remote_filename("a/b/.."), "download.bin")
        with self.assertRaises(ValueError):
            validate_custom_filename("trailing.")

        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as outside_dir:
            link_path = os.path.join(base_dir, "link.bin")
            os.symlink(os.path.join(outside_dir, "secret.bin"), link_path)
            with self.assertRaises(ValueError):
                ensure_within_directory(base_dir, link_path)


if __name__ == "__main__":
    unittest.main()
