"""Entry point for the downloadable app (PyInstaller): the same as the
`madhippu` command, so double-clicking it opens the page in the browser."""

import sys

from madhippu.cli import main

sys.exit(main())
