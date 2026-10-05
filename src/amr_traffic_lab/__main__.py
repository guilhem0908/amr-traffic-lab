"""Allow ``python -m amr_traffic_lab``."""

import sys

from .cli import main

sys.exit(main())
