from source_health.probes.fund_provider import probe_fund_provider, probe_provider_capability
from source_health.probes.news_source import probe_news_source
from source_health.probes.data_source_adapter import probe_data_source_adapter


def probe_registered_provider(provider, capability_id, **kwargs):
    """Use contract-adapter health metadata when available; preserve legacy probes."""
    if getattr(provider, "descriptor", None) is not None and callable(getattr(provider, "probe", None)):
        return probe_data_source_adapter(provider, capability_id)
    return probe_provider_capability(provider, capability_id, **kwargs)

__all__ = ["probe_data_source_adapter", "probe_fund_provider", "probe_provider_capability", "probe_registered_provider", "probe_news_source"]
