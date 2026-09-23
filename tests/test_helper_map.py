"""Helper map coverage: every top-level helper symbol is indexed.

The map in docs/agents.md is the only index into the single-file
helper, so a helper change that adds a top-level def or class without
indexing it leaves the map stale. This reads both files as plain text and
fails on any top-level name the map never mentions.

Method-level symbols (e.g. Link.notify_change) are outside this sweep: only
module-level `def` and `class` lines count, whatever methods the map names.
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(os.path.dirname(HERE), "bin", "sony-headphones")
MAP = os.path.join(os.path.dirname(HERE), "docs", "agents.md")

TOP_LEVEL = re.compile(r"^(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def top_level_names(helper_text):
    return TOP_LEVEL.findall(helper_text)


def missing_from_map(names, map_text):
    return sorted({name for name in names if name not in map_text})


class TestHelperMap(unittest.TestCase):
    def test_every_top_level_symbol_is_indexed(self):
        with open(HELPER, encoding="utf-8") as handle:
            helper_text = handle.read()
        with open(MAP, encoding="utf-8") as handle:
            map_text = handle.read()
        names = top_level_names(helper_text)
        self.assertGreater(len(names), 0)
        self.assertEqual(missing_from_map(names, map_text), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
