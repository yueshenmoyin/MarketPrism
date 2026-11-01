"""Backwards-compatibility shim for the renamed module.

The agent is now ``sentiment_analyst`` and chooses market-appropriate
sentiment sources for A-share, Hong Kong, and US tickers. Import from
``aitrade.agents.analysts.sentiment_analyst`` going forward; this module
will be removed in a future release.

"""

import warnings as _warnings

from aitrade.agents.analysts.sentiment_analyst import (  # noqa: F401
    create_sentiment_analyst,
    create_social_media_analyst,
)

_warnings.warn(
    "aitrade.agents.analysts.social_media_analyst is deprecated. "
    "Import from aitrade.agents.analysts.sentiment_analyst instead.",
    DeprecationWarning,
    stacklevel=2,
)
