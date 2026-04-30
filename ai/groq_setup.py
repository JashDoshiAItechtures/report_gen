"""DSPy language model setup for Groq (and optionally OpenAI).

We configure DSPy ONCE at import time on the main thread to avoid
thread-safety issues with FastAPI's worker threads.
"""

import dspy
import config


def _configure_default_lm() -> dspy.LM:
    """Configure the global DSPy LM once and return it."""
    lm = dspy.LM(
        model=f"groq/{config.GROQ_MODEL}",
        api_key=config.GROQ_API_KEY,
        max_tokens=4096,
        temperature=0,
        seed=42,
        timeout=config.LLM_TIMEOUT_SECONDS,
    )
    dspy.configure(lm=lm)
    return lm


_DEFAULT_LM = _configure_default_lm()


def get_lm(provider: str = "groq") -> dspy.LM:
    """Return the LM instance to use.

    NOTE: Currently only the Groq backend is implemented. If a different
    provider is requested, a warning is emitted and Groq is returned as the
    fallback.  Extend this function to add OpenAI / other providers.
    """
    import logging as _logging
    if provider != "groq":
        _logging.getLogger(__name__).warning(
            "Provider '%s' is not yet implemented — falling back to Groq.", provider
        )
    return _DEFAULT_LM
