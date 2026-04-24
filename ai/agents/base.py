"""BaseAgent — shared async infrastructure for all report agents.

Provides:
  - run_in_executor()  wrap any blocking/sync call into the thread pool
  - timed()            context manager that records wall-clock timing
  - logger             per-agent named logger
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Callable


class BaseAgent:
    """Abstract base for report pipeline agents.

    Subclasses implement:
        async def run(self, blueprint: dict, question: str) -> dict

    The returned dict is merged (shallow) into the final report response.
    """

    #: Override in each subclass for clean log prefixes
    name: str = "agent"

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"agent.{self.name}")
        self._elapsed: float = 0.0

    # ── Thread-pool bridge ────────────────────────────────────────────────────

    async def run_in_executor(self, fn: Callable, *args) -> Any:
        """Run a blocking synchronous function in the default thread pool.

        This is essential to keep the asyncio event loop unblocked while
        psycopg2 / SQLAlchemy are waiting on network I/O from the DB.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def run_many(self, fn: Callable, items: list) -> list:
        """Run fn(item) for every item in the list concurrently.

        Returns results in the same order as items.
        """
        tasks = [self.run_in_executor(fn, item) for item in items]
        return await asyncio.gather(*tasks)

    # ── Timing helper ─────────────────────────────────────────────────────────

    @asynccontextmanager
    async def timed(self):
        """Async context manager that records elapsed time in self._elapsed."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._elapsed = time.perf_counter() - t0
            self.logger.info(
                "[%s] finished in %.3fs", self.name, self._elapsed
            )

    # ── Interface (must be implemented by subclasses) ─────────────────────────

    async def run(self, blueprint: dict, question: str) -> dict:  # pragma: no cover
        """Execute the agent and return a partial report dict.

        Args:
            blueprint: The parsed JSON report blueprint from the LLM.
            question:  The original user question (for topic detection etc.)

        Returns:
            A dict whose keys will be merged into the final report.
            The dict MUST also contain an "agent_timing" key:
                {"agent_timing": {"<name>": <elapsed_seconds>}}
        """
        raise NotImplementedError
