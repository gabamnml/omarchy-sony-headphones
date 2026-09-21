#!/usr/bin/env python3
"""Run the whole test suite by discovery; exit non-zero on any failure.

    python3 tests/run.py

The modules are also runnable one at a time, e.g. `python3 tests/test_v1.py`;
this just runs all of them the same way `unittest discover` would.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    sys.path.insert(0, HERE)
    suite = unittest.TestLoader().discover(HERE, top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
