"""Q2 security suite isolation: set data root before importing backend modules."""
from __future__ import annotations

import os
import tempfile

os.environ["TZTUZHAN_DATA_DIR"] = tempfile.mkdtemp(prefix="tztuzhan-q2-security-")
os.environ["MEMORY_V2"] = "0"
