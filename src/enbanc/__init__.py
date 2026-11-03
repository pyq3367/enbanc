"""enbanc -- long-haul verification for high-value artifacts.

The loop:
    produce several artifacts -> check them mechanically -> judge them
    independently -> escalate the judge if it keeps finding new problems
    -> refuse to call it delivered until someone passes it.

Nothing here calls a model. Wrap your own in a Provider.
"""

from .arbiter import escalate, review
from .dispatch import Task, dispatch
from .mandate import Mandate
from .route import route
from .state import DERIVED, OUTCOMES, STAGES, Run, extract_tail_json, normalize_report

__version__ = "0.1.0"

__all__ = [
    "Run", "Task", "dispatch", "review", "escalate", "Mandate", "route",
    "extract_tail_json", "normalize_report", "STAGES", "OUTCOMES", "DERIVED",
    "__version__",
]
