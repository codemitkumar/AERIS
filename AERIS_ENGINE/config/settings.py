"""External research-data location.

Reference datasets too large to ship in the repo (EUROCONTROL R&D sample,
NASA DASHlink FDR archive, OpenSky in-flight-emergency dataset) live on a
separate drive rather than under AERIS_ENGINE/. Set AERIS_DATA_ROOT if your
copy lives somewhere other than the default below.
"""

import os
from pathlib import Path

DATA_ROOT = Path(os.environ.get("AERIS_DATA_ROOT", r"D:\Sanju\ResearchData"))

ADRR_DIR     = DATA_ROOT / "adrrData"
DASHLINK_DIR = DATA_ROOT / "dashlink"
OPENSKY_DIR  = DATA_ROOT / "opensky_emergency"


class ResearchDataUnavailableError(FileNotFoundError):
    """DATA_ROOT isn't reachable — drive not connected or path misconfigured.

    Subclasses FileNotFoundError so existing `except FileNotFoundError`
    call sites (e.g. emergencyInjector/notam/airport_closure.py) already
    degrade gracefully without change.
    """


_warned = False


def check_data_root(quiet: bool = False) -> bool:
    """Return True if DATA_ROOT is reachable. Prints a one-time [AERIS]
    warning banner on first failure per process unless quiet=True."""
    global _warned
    ok = DATA_ROOT.is_dir()
    if not ok and not quiet and not _warned:
        _warned = True
        print(
            f"[AERIS] WARNING: research data drive not found at {DATA_ROOT} -- "
            f"EUROCONTROL EU data, DASHlink FDR archive, and the OpenSky "
            f"emergency dataset are unavailable this run. Connect the drive, "
            f"or set AERIS_DATA_ROOT to override the path."
        )
    return ok


def require_data_root() -> Path:
    """Return DATA_ROOT, raising ResearchDataUnavailableError if unreachable."""
    if not check_data_root():
        raise ResearchDataUnavailableError(
            f"Research data drive not found at {DATA_ROOT}. "
            f"Connect it or set the AERIS_DATA_ROOT environment variable."
        )
    return DATA_ROOT
