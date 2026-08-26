import { decodeIndustryResearchResponse, type IndustryResearchResponse } from "@/lib/api";

type Wire = Record<string, any>;

const GENERATED_AT = "2026-08-25T00:00:00+00:00";

function evidenceWire(metricId: string, suffix: string, official = true): Wire {
  return {
    evidence_id: `E-${metricId}-${suffix}`,
    source_family_id: `family-${suffix}`,
    content_source: `source-${suffix}.example`,
    origin_cluster: `origin-${suffix}`,
    collector_source: `collector-${suffix}`,
    final_url: `https://source-${suffix}.example/${metricId}`,
    is_official: official,
    is_official_attested: official,
    supports_claim: true,
    supports_fields: [metricId],
    contradicts_claim: false,
    as_of_date: "2026-08-24",
    verified_at: "2026-08-25T00:00:00+00:00",
  };
}

function trustedMetricWire(industryId: string, metricId: string, label: string, corroborated = false): Wire {
  const evidence = corroborated
    ? [evidenceWire(metricId, "public-a", false), evidenceWire(metricId, "public-b", false)]
    : [evidenceWire(metricId, "official", true)];
  return {
    industry_id: industryId,
    metric_id: metricId,
    label,
    current_value: metricId === "nand_price" ? 96.4 : metricId === "hbm_demand" ? "需求有证据支持" : 108.2,
    unit: metricId === "hbm_demand" ? null : "隔离演示指数",
    change: { value: metricId === "nand_price" ? -1.2 : 4.2, basis: "mom" },
    historical_position: metricId === "hbm_demand" ? null : { value: 72, window: "36 个月", method: "隔离演示分位" },
    availability_status: "available",
    verification_status: corroborated ? "corroborated" : "verified",
    freshness_status: "fresh",
    source_run_status: "healthy",
    empty_reason: null,
    as_of_date: "2026-08-24",
    fetched_at: "2026-08-25T00:00:00+00:00",
    methodology: "同口径公开快照；隔离测试值",
    judgment_basis: [`${metricId} 已有准入证据支持`],
    invalidating_conditions: [`${metricId} 来源撤回或口径变化`],
    evidence,
    independent_source_families: evidence.map((item) => item.source_family_id),
    independent_content_sources: evidence.map((item) => item.content_source),
    independent_origin_clusters: evidence.map((item) => item.origin_cluster),
    raw_snapshot_id: `RAW-${industryId}-1`,
    evidence_snapshot_id: `EVIDENCE-${industryId}-1`,
    expires_at: null,
  };
}

function emptyMetricWire(industryId: string, metricId: string, label: string, reason = "source_unconfigured"): Wire {
  return {
    industry_id: industryId,
    metric_id: metricId,
    label,
    current_value: null,
    unit: null,
    change: null,
    historical_position: null,
    availability_status: reason === "source_unconfigured" ? "unconfigured" : "unavailable",
    verification_status: "not_evaluated",
    freshness_status: "unknown",
    source_run_status: reason === "source_failed" ? "failed" : "not_configured",
    empty_reason: reason,
    as_of_date: null,
    fetched_at: null,
    methodology: "尚无可准入来源，保留模板字段",
    judgment_basis: [],
    invalidating_conditions: [],
    evidence: [],
    independent_source_families: [],
    independent_content_sources: [],
    independent_origin_clusters: [],
    raw_snapshot_id: null,
    evidence_snapshot_id: null,
    expires_at: null,
  };
}

const layouts = {
  storage: {
    cycle: [
      ["dram_price", "DRAM 价格"], ["nand_price", "NAND 价格"], ["hbm_demand", "HBM 需求"],
      ["inventory_level", "库存水平"], ["capacity_utilization", "产能利用率"],
      ["manufacturer_capex", "厂商资本开支"], ["server_demand", "服务器需求"],
      ["consumer_electronics_demand", "消费电子需求"],
    ],
    chain: ["设备与材料", "存储设计与制造", "封装测试", "模组与控制器", "服务器 / 手机 / PC / 汽车终端"],
  },
  semiconductor: {
    cycle: [["equipment_orders", "设备订单"], ["materials_demand", "材料需求"], ["design_inventory", "设计库存"], ["fab_utilization", "晶圆厂利用率"], ["advanced_packaging", "先进封装需求"], ["terminal_demand", "终端需求"], ["localization", "国产替代进度"], ["capex", "资本开支"]],
    chain: ["设备与材料", "芯片设计", "晶圆制造", "封装测试", "终端需求"],
  },
  robotics: {
    cycle: [["prototype_progress", "样机进展"], ["orders", "订单"], ["deliveries", "交付"], ["mass_production", "量产进度"], ["output", "产量"], ["sales", "销量"], ["component_cost", "核心零部件成本"], ["applications", "下游应用"]],
    chain: ["核心零部件", "整机", "软件与机器视觉", "应用"],
  },
} as const;

function conclusionWire(industryId: string, trusted: Wire[]): Wire {
  const complete = industryId === "storage";
  const basis = complete ? trusted.filter((item) => ["dram_price", "nand_price"].includes(item.metric_id)) : [];
  const evidenceIds = basis.flatMap((item) => item.evidence.map((evidence: Wire) => evidence.evidence_id));
  const conditions = basis.flatMap((item) => item.invalidating_conditions);
  const status = complete ? "verified" : "unavailable";
  const cycleStage = complete ? "recovery" : null;
  const direction = complete ? "improving" : null;
  const confidence = complete ? "medium" : null;
  const ratio = complete ? 1 : 0;
  return {
    conclusion_id: `CONCLUSION-${industryId}-1`, industry_id: industryId,
    rule_version: `${industryId}-cycle-v1`, status,
    cycle_stage: cycleStage, outlook_direction: direction, confidence_level: confidence,
    data_completeness: { verified_metric_count: complete ? 2 : 0, required_metric_count: 2, ratio },
    text: `规则=${industryId}-cycle-v1；状态=${status}；周期=${cycleStage ?? "暂无可靠数据"}；方向=${direction ?? "暂无可靠数据"}；可信度=${confidence ?? "暂无可靠数据"}；完整度=${ratio.toFixed(2)}；证据=${evidenceIds.length ? evidenceIds.join(",") : "无"}`,
    basis_metric_ids: basis.map((item) => item.metric_id),
    evidence_ids: evidenceIds,
    invalidating_conditions: conditions,
  };
}

function candidateWire(industryId: string): Wire {
  const prefix = industryId.toUpperCase();
  const unverified = {
    ...trustedMetricWire(industryId, `${industryId}_candidate_metric`, "待核验候选指标"),
    verification_status: "unverified",
    raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2`,
  };
  return {
    industry_id: industryId,
    candidate_snapshot_id: `CANDIDATE-${prefix}-1`,
    counts: { unverified: 1, conflicting: 1, unverified_events: 2, conflicting_events: 1 },
    unverified: [unverified],
    conflicting: [{
      industry_id: industryId, metric_id: `${industryId}_conflict`, aggregate_value: null,
      source_values: [
        { evidence_id: "E-CONFLICT-A", source_family_id: "family-a", value: "上升", unit: null, as_of_date: "2026-08-24", change: { value: 2.1, basis: "mom" } },
        { evidence_id: "E-CONFLICT-B", source_family_id: "family-b", value: "下降", unit: null, as_of_date: "2026-08-23", change: { value: -1.8, basis: "mom" } },
      ], raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2`,
    }],
    unverified_events: [
      { industry_id: industryId, event_id: `${prefix}-CANDIDATE-RECENT`, status: "unverified", occurred_at: "2026-08-24T00:00:00+00:00", evidence_ids: ["E-CANDIDATE-RECENT"], supporting_evidence_ids: ["E-CANDIDATE-RECENT"], contradicting_evidence_ids: [], roles: ["news"], candidate_snapshot_id: `CANDIDATE-${prefix}-1`, raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2` },
      { industry_id: industryId, event_id: `${prefix}-CANDIDATE-OLD`, status: "unverified", occurred_at: "2026-05-01T00:00:00+00:00", evidence_ids: ["E-CANDIDATE-OLD"], supporting_evidence_ids: ["E-CANDIDATE-OLD"], contradicting_evidence_ids: [], roles: ["risk"], candidate_snapshot_id: `CANDIDATE-${prefix}-1`, raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2` },
    ],
    conflicting_events: [{ industry_id: industryId, event_id: `${prefix}-CONFLICT-30`, status: "conflicting", occurred_at: "2026-08-05T00:00:00+00:00", evidence_ids: ["E-CONFLICT-A", "E-CONFLICT-B"], supporting_evidence_ids: ["E-CONFLICT-A"], contradicting_evidence_ids: ["E-CONFLICT-B"], roles: ["risk"], candidate_snapshot_id: `CANDIDATE-${prefix}-1`, raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2` }],
    raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2`,
  };
}

export function industryResponseWire(industryId = "storage", options: {
  generatedAt?: string | null;
  phase?: "idle" | "collecting" | "verifying" | "failed" | "trusted_published";
  demo?: boolean;
} = {}): Wire {
  const layout = layouts[industryId as keyof typeof layouts];
  if (!layout) {
    return {
      requested_industry_id: industryId, displayed_industry_id: null, displayed_trusted_report: null,
      candidate_evidence: null,
      refresh_run: { industry_id: industryId, run_id: null, raw_snapshot_id: null, evidence_snapshot_id: null, candidate_snapshot_id: null, phase: "idle", error_code: null, displayed_trusted_snapshot_id: null, published_trusted_snapshot_id: null, displayed_raw_snapshot_id: null, displayed_evidence_snapshot_id: null },
      template_status: "building",
    };
  }
  const cycle = layout.cycle.map(([metricId, label]) => {
    if (industryId === "storage" && metricId === "dram_price") return trustedMetricWire(industryId, metricId, label);
    if (industryId === "storage" && metricId === "nand_price") return trustedMetricWire(industryId, metricId, label);
    if (industryId === "storage" && metricId === "hbm_demand") return trustedMetricWire(industryId, metricId, label, true);
    return emptyMetricWire(industryId, metricId, label);
  });
  const trusted = cycle.filter((item) => item.current_value !== null);
  const prefix = industryId.toUpperCase();
  const candidate = candidateWire(industryId);
  const report = {
    industry_id: industryId, template_status: "complete_layout",
    trusted_snapshot_id: `TRUSTED-${prefix}-1`, displayed_trusted_snapshot_id: `TRUSTED-${prefix}-1`,
    raw_snapshot_id: `RAW-${industryId}-1`, evidence_snapshot_id: `EVIDENCE-${industryId}-1`,
    generated_at: options.generatedAt === undefined ? GENERATED_AT : options.generatedAt,
    demo: options.demo ?? true,
    source_coverage: { unit: "capability", total: 8, configured: 4, healthy: 3, partial_failure: 0, failed: 1, unconfigured: 4 },
    counts: { verified: trusted.filter((item) => item.verification_status === "verified").length, corroborated: trusted.filter((item) => item.verification_status === "corroborated").length },
    overview: conclusionWire(industryId, trusted),
    cycle, chain: layout.chain.map((label, index) => ({ industry_id: industryId, node_id: `${industryId}-chain-${index + 1}`, label, observation_ids: [], evidence_ids: [], status: "unavailable" })),
    metrics: structuredClone(cycle),
    capital: [
      emptyMetricWire(industryId, `${industryId}_sector_flow`, "板块资金", "source_failed"),
      emptyMetricWire(industryId, `${industryId}_etf_share`, "ETF 份额"),
      emptyMetricWire(industryId, `${industryId}_valuation_level`, "估值水平"),
      emptyMetricWire(industryId, `${industryId}_valuation_percentile`, "历史分位"),
    ],
    companies: industryId === "storage" ? [{ industry_id: industryId, security_code: "DEMO-SEC-001", company_name: "隔离演示公司", chain_node_id: "storage-chain-2", relation_type: "official_disclosure", key_metric_ids: ["dram_price"], evidence_ids: ["E-COMPANY-1"], as_of_date: "2026-08-24", observation_only: true }] : [],
    fund_selection: [], funds: [],
    news_risk: [
      { industry_id: industryId, event_id: `${prefix}-NEWS-7`, status: "verified", occurred_at: "2026-08-24T00:00:00+00:00", evidence_ids: ["E-NEWS-7"], roles: ["news", "catalyst"] },
      { industry_id: industryId, event_id: `${prefix}-NEWS-30`, status: "corroborated", occurred_at: "2026-08-01T00:00:00+00:00", evidence_ids: ["E-NEWS-30-A", "E-NEWS-30-B"], roles: ["risk"] },
      { industry_id: industryId, event_id: `${prefix}-NEWS-90`, status: "verified", occurred_at: "2026-06-20T00:00:00+00:00", evidence_ids: ["E-NEWS-90"], roles: ["reverse_signal"] },
      { industry_id: industryId, event_id: `${prefix}-NEWS-FUTURE`, status: "verified", occurred_at: "2026-08-26T00:00:00+00:00", evidence_ids: ["E-NEWS-FUTURE"], roles: ["news"] },
    ],
  };
  const phase = options.phase ?? "verifying";
  const published = phase === "trusted_published" ? report.displayed_trusted_snapshot_id : null;
  return {
    requested_industry_id: industryId, displayed_industry_id: industryId,
    displayed_trusted_report: report, candidate_evidence: candidate,
    refresh_run: {
      industry_id: industryId, run_id: `RUN-${industryId}`, raw_snapshot_id: `RAW-${industryId}-2`, evidence_snapshot_id: `EVIDENCE-${industryId}-2`, candidate_snapshot_id: candidate.candidate_snapshot_id,
      phase, error_code: phase === "failed" ? "partial_source_failure" : null,
      displayed_trusted_snapshot_id: report.displayed_trusted_snapshot_id,
      published_trusted_snapshot_id: published,
      displayed_raw_snapshot_id: report.raw_snapshot_id,
      displayed_evidence_snapshot_id: report.evidence_snapshot_id,
    },
    template_status: "complete_layout",
  };
}

export function researchResponse(industryId = "storage", options: Parameters<typeof industryResponseWire>[1] = {}): IndustryResearchResponse {
  return decodeIndustryResearchResponse(industryResponseWire(industryId, options), new Date("2026-08-25T12:00:00+00:00"));
}

export function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}
