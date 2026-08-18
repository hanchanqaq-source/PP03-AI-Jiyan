from __future__ import annotations

from dataclasses import dataclass

from source_health.models import ProbeObservation, RepairValue


@dataclass(frozen=True)
class RepairAdvice:
    value: RepairValue
    reason: str


def advise_repair(
    observation: ProbeObservation,
    *,
    permanent_redirect_same_public_source: bool = False,
    missing_standard_request_headers: bool = False,
    confirmed_compatibility_issue: bool = False,
    source_id_conflict: bool = False,
    duplicate_configuration: bool = False,
    cache_status_mislabeled: bool = False,
    high_value_source: bool = False,
    reproducible_failure: bool = False,
    public_entry_changed: bool = False,
    reliable_fallback_available: bool | None = None,
    retry_after_present: bool = False,
    reliable_cache_available: bool = False,
    domain_long_unresolvable: bool = False,
    public_feed_removed: bool = False,
    failure_days: int = 0,
    requires_permission_bypass: bool = False,
    requires_tls_bypass: bool = False,
    duplicate_without_independent_track: bool = False,
    content_label_mismatch_long_term: bool = False,
    compliance_issue: bool = False,
) -> RepairAdvice:
    status = observation.http_status

    if compliance_issue:
        return RepairAdvice("disable_candidate", "存在访问合规问题，建议停用候选配置")
    if duplicate_without_independent_track:
        return RepairAdvice("disable_candidate", "配置完全重复且没有独立赛道意义")
    if content_label_mismatch_long_term:
        return RepairAdvice("disable_candidate", "内容与配置标签长期不符")

    # Authentication, permission and TLS-bypass cases are never eligible for an automatic fix.
    if status in {401, 403} or requires_permission_bypass:
        return RepairAdvice("replace_candidate", "来源需要权限访问，建议评估公开替代来源")
    if requires_tls_bypass:
        return RepairAdvice("replace_candidate", "修复需要绕过 TLS 校验，建议评估替代来源")

    if observation.redirected and permanent_redirect_same_public_source:
        return RepairAdvice("immediate_fix", "永久跳转到同一公开来源的新地址")
    if missing_standard_request_headers:
        return RepairAdvice("immediate_fix", "缺少标准请求头且已确认导致失败")
    if confirmed_compatibility_issue and observation.error_type != "tls":
        return RepairAdvice("immediate_fix", "已确认编码、gzip、RSS 或 Atom 兼容问题")
    if source_id_conflict:
        return RepairAdvice("immediate_fix", "来源 ID 冲突")
    if duplicate_configuration:
        return RepairAdvice("immediate_fix", "存在完全相同的重复配置")
    if cache_status_mislabeled:
        return RepairAdvice("immediate_fix", "缓存状态误标")

    if high_value_source and reproducible_failure and observation.error_type in {"schema_changed", "parse"}:
        return RepairAdvice("worth_fixing", "高价值来源存在可复现的结构或解析失败")

    if status in {404, 410}:
        return RepairAdvice("replace_candidate", f"公开入口返回 HTTP {status}")
    if domain_long_unresolvable:
        return RepairAdvice("replace_candidate", "域名长期不可解析")
    if public_feed_removed:
        return RepairAdvice("replace_candidate", "页面不再提供公开 Feed")
    if failure_days > 1 and not reliable_cache_available:
        return RepairAdvice("replace_candidate", "连续多日失败且无可靠缓存")

    if observation.error_type in {"timeout", "dns", "tls"} and failure_days <= 1:
        return RepairAdvice("observe", "单次网络或 TLS 故障，继续观察")
    if status is not None and 500 <= status <= 599 and failure_days <= 1:
        return RepairAdvice("observe", "单次服务端错误，继续观察")
    if status == 429 and retry_after_present:
        return RepairAdvice("observe", "来源提供 Retry-After，按提示继续观察")
    if reliable_cache_available:
        return RepairAdvice("observe", "当前可使用可靠缓存")

    if public_entry_changed:
        return RepairAdvice("worth_fixing", "公开入口变化，需要有限适配")
    fallback = observation.fallback_available if reliable_fallback_available is None else reliable_fallback_available
    if observation.probe_status != "success" and not fallback:
        return RepairAdvice("worth_fixing", "同一能力没有其他可靠备用源")

    return RepairAdvice("none", "当前健康或没有足够证据")


def apply_repair_advice(observation: ProbeObservation, **evidence: object) -> ProbeObservation:
    advice = advise_repair(observation, **evidence)
    observation.repair_value = advice.value
    observation.repair_reason = advice.reason
    return observation
