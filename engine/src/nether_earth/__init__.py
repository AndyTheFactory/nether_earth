"""Pure-Python Nether Earth engine package.

Gameplay rules belong here. Network, FastAPI, renderer, and deployment concerns do not.
"""

__all__ = ["engine_identity"]


def engine_identity() -> str:
    """Return a deterministic smoke-test value for the package boundary."""
    return "nether-earth-engine"
