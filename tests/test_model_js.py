"""Model.js under node or deno, the panel's decisions without a QML engine.

The assertions live in tests/test_model.mjs so the same file runs by hand
under either runtime; this only picks whichever one is installed.
"""

import os
import shutil
import subprocess
import unittest


class TestModelJs(unittest.TestCase):
    """Model.js makes the panel's decisions; these run it without a QML engine.

    The assertions live in tests/test_model.mjs so the same file runs by hand
    under either runtime; this only picks whichever one is installed.
    """

    TEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "test_model.mjs")

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        deno = shutil.which("deno")
        if node is not None:
            cls.command = [node, cls.TEST]
        elif deno is not None:
            cls.command = [deno, "run", "--allow-read", cls.TEST]
        else:
            raise unittest.SkipTest("neither node nor deno is installed")

    def test_the_model_suite_passes(self):
        result = subprocess.run(self.command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all model tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
