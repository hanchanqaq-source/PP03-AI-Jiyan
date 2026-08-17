from __future__ import annotations

from typing import Any

import newsradar


def probe_news_source(source: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return newsradar.probe_source_config(source, **kwargs)
