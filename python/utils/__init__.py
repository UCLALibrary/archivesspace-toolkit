"""
Re-export generic utilities so they can be imported directly from the utils package.

E.g. `from utils import configure_logging` vs `from utils.generic_utils import configure_logging`.
"""

from .generic_utils import configure_logging, load_config

__all__ = ["configure_logging", "load_config"]
