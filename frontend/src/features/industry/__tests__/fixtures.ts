import type {
  CandidateIndustryEvidence,
  DisplayedIndustryReport,
  IndustryMetric,
  IndustryResearchResponse,
} from "@/lib/api";

export function metric(overrides: Partial<IndustryMetric> = {}): IndustryMetric {
  return {
    industryId: "storage",
    metricId: "dram_price",
    label: "DRAM 价格",
    currentValue: 108.2,
    unit: "演示指数",
    change: { value: 4.2, basis: "mom" },
    historicalPosition: { value: 72, window: "36 个月", method: "隔离演示分位" },
    availabilityStatus: "available",
    verificationStatus: "verified",
    freshnessStatus: "fresh",
    sourceRunStatus: "healthy",
    emptyReason: null,
    asOfDate: "2026-08-20T00:00:00+00:00",
    fetchedAt: "2026-08-20T01:00:00+00:00",
    methodology: "同口径公开快照；隔离测试值",
    judgmentBasis: ["DRAM 月度快照支持"],
    invalidatingConditions: ["来源撤回或口径变化"],
    evidence: [{
      evidenceId: "E-STORAGE-1",
      sourceFamilyId: "official-family",
      contentSource: "隔离演示官方披露",
      originCluster: "official-origin",
      collectorSource: "fixture-collector",
      finalUrl: "https://example.com/storage-evidence",
      isOfficial: true,
      isOfficialAttested: true,
      supportsClaim: true,
      supportsFields: ["current_value"],
      contradictsClaim: false,
      asOfDate: "2026-08-20T00:00:00+00:00",
      verifiedAt: "2026-08-20T02:00:00+00:00",
    }],
    independentSourceFamilies: ["official-family"],
    independentContentSources: ["隔离演示官方披露"],
    independentOriginClusters: ["official-origin"],
    rawSnapshotId: "RAW-STORAGE-1",
    evidenceSnapshotId: "EVIDENCE-STORAGE-1",
    expiresAt: "2026-09-20T00:00:00+00:00",
    ...overrides,
  };
}

export function displayedReport(
  industryId = "storage",
  overrides: Partial<DisplayedIndustryReport> = {},
): DisplayedIndustryReport {
  const prefix = industryId.toUpperCase();
  const labels = industryId === "robotics"
    ? { metric: "整机交付", chain: "核心零部件" }
    : industryId === "semiconductor"
      ? { metric: "晶圆厂利用率", chain: "晶圆制造" }
      : { metric: "DRAM 价格", chain: "存储设计与制造" };
  const primaryMetric = metric({ industryId, label: labels.metric, metricId: `${industryId}_primary` });
  const missingMetric = metric({
    industryId, metricId: `${industryId}_missing`, label: industryId === "storage" ? "NAND 价格" : "需求验证",
    currentValue: null, unit: null, change: null, historicalPosition: null,
    availabilityStatus: "unconfigured", verificationStatus: "not_evaluated",
    freshnessStatus: "unknown", sourceRunStatus: "not_configured", emptyReason: "source_unconfigured",
    asOfDate: null, fetchedAt: null, evidence: [], independentSourceFamilies: [],
    independentContentSources: [], independentOriginClusters: [], rawSnapshotId: null,
    evidenceSnapshotId: null, expiresAt: null,
  });
  return {
    industryId,
    templateStatus: "complete_layout",
    trustedSnapshotId: `TRUSTED-${prefix}-1`,
    displayedTrustedSnapshotId: `TRUSTED-${prefix}-1`,
    rawSnapshotId: `RAW-${prefix}-1`,
    evidenceSnapshotId: `EVIDENCE-${prefix}-1`,
    generatedAt: "2026-08-21T00:00:00+00:00",
    demo: true,
    sourceCoverage: { unit: "capability", total: 8, configured: 4, healthy: 3, partialFailure: 0, failed: 1, unconfigured: 4 },
    counts: { verified: 3, corroborated: 1 },
    overview: {
      conclusionId: `CONCLUSION-${prefix}-1`, industryId, ruleVersion: `${industryId}-cycle-v1`, status: "verified",
      cycleStage: "recovery", outlookDirection: "improving", confidenceLevel: "medium",
      dataCompleteness: { verifiedMetricCount: 6, requiredMetricCount: 8, ratio: 0.75 },
      text: "隔离演示：当前周期结论仅由已准入指标确定。",
      basisMetricIds: [primaryMetric.metricId], evidenceIds: ["E-STORAGE-1"],
      invalidatingConditions: ["已准入指标同步转弱"],
    },
    cycle: [primaryMetric, missingMetric],
    chain: [{ industryId, nodeId: `${industryId}-chain`, label: labels.chain, observationIds: [primaryMetric.metricId], evidenceIds: ["E-STORAGE-1"], status: "verified" }],
    metrics: [primaryMetric, missingMetric],
    capital: [metric({
      industryId, metricId: "sector_flow", label: "板块资金", currentValue: null, unit: null,
      change: null, historicalPosition: null, availabilityStatus: "unavailable",
      verificationStatus: "not_evaluated", freshnessStatus: "unknown", sourceRunStatus: "failed",
      emptyReason: "source_failed", asOfDate: null, fetchedAt: null, evidence: [],
      independentSourceFamilies: [], independentContentSources: [], independentOriginClusters: [],
      rawSnapshotId: null, evidenceSnapshotId: null, expiresAt: null,
    })],
    companies: [{ industryId, securityCode: "DEMO-SEC-001", companyName: "隔离演示公司", chainNodeId: `${industryId}-chain`, relationType: "official_disclosure", keyMetricIds: [primaryMetric.metricId], evidenceIds: ["E-STORAGE-1"], asOfDate: "2026-08-20T00:00:00+00:00", observationOnly: true }],
    fundSelection: [], funds: [],
    newsRisk: [
      { industryId, eventId: `${prefix}-NEWS-7`, status: "verified", occurredAt: "2026-08-20T00:00:00+00:00", evidenceIds: ["E-NEWS-7"], roles: ["news", "catalyst"] },
      { industryId, eventId: `${prefix}-NEWS-30`, status: "corroborated", occurredAt: "2026-08-01T00:00:00+00:00", evidenceIds: ["E-NEWS-30-A", "E-NEWS-30-B"], roles: ["risk"] },
      { industryId, eventId: `${prefix}-NEWS-90`, status: "verified", occurredAt: "2026-06-20T00:00:00+00:00", evidenceIds: ["E-NEWS-90"], roles: ["reverse_signal"] },
    ],
    ...overrides,
  };
}

export function candidateEvidence(industryId = "storage"): CandidateIndustryEvidence {
  const prefix = industryId.toUpperCase();
  return {
    industryId, candidateSnapshotId: `CANDIDATE-${prefix}-1`,
    counts: { unverified: 1, conflicting: 1, unverifiedEvents: 1, conflictingEvents: 1 },
    unverified: [metric({ industryId, metricId: "hbm_demand", label: "HBM 需求候选", currentValue: "候选上升", verificationStatus: "unverified", rawSnapshotId: `RAW-${prefix}-2`, evidenceSnapshotId: `EVIDENCE-${prefix}-2` })],
    conflicting: [{ industryId, metricId: "inventory_level", aggregateValue: null, sourceValues: [
      { evidenceId: "E-CONFLICT-A", sourceFamilyId: "family-a", value: "上升", unit: null, asOfDate: "2026-08-20T00:00:00+00:00", change: null },
      { evidenceId: "E-CONFLICT-B", sourceFamilyId: "family-b", value: "下降", unit: null, asOfDate: "2026-08-20T00:00:00+00:00", change: null },
    ], rawSnapshotId: `RAW-${prefix}-2`, evidenceSnapshotId: `EVIDENCE-${prefix}-2` }],
    unverifiedEvents: [{ industryId, eventId: `${prefix}-CANDIDATE-7`, status: "unverified", occurredAt: "2026-08-19T00:00:00+00:00", evidenceIds: ["E-CANDIDATE-7"], supportingEvidenceIds: ["E-CANDIDATE-7"], contradictingEvidenceIds: [], roles: ["news"], candidateSnapshotId: `CANDIDATE-${prefix}-1`, rawSnapshotId: `RAW-${prefix}-2`, evidenceSnapshotId: `EVIDENCE-${prefix}-2` }],
    conflictingEvents: [{ industryId, eventId: `${prefix}-CONFLICT-30`, status: "conflicting", occurredAt: "2026-08-05T00:00:00+00:00", evidenceIds: ["E-CONFLICT-A", "E-CONFLICT-B"], supportingEvidenceIds: ["E-CONFLICT-A"], contradictingEvidenceIds: ["E-CONFLICT-B"], roles: ["risk"], candidateSnapshotId: `CANDIDATE-${prefix}-1`, rawSnapshotId: `RAW-${prefix}-2`, evidenceSnapshotId: `EVIDENCE-${prefix}-2` }],
    rawSnapshotId: `RAW-${prefix}-2`, evidenceSnapshotId: `EVIDENCE-${prefix}-2`,
  };
}

export function researchResponse(industryId = "storage", overrides: Partial<IndustryResearchResponse> = {}): IndustryResearchResponse {
  const report = displayedReport(industryId);
  const candidate = candidateEvidence(industryId);
  return {
    requestedIndustryId: industryId, displayedIndustryId: industryId, displayedTrustedReport: report,
    candidateEvidence: candidate,
    refreshRun: {
      industryId, runId: `RUN-${industryId}`, rawSnapshotId: candidate.rawSnapshotId,
      evidenceSnapshotId: candidate.evidenceSnapshotId, candidateSnapshotId: candidate.candidateSnapshotId,
      phase: "failed", errorCode: "partial_source_failure",
      displayedTrustedSnapshotId: report.displayedTrustedSnapshotId, publishedTrustedSnapshotId: null,
      displayedRawSnapshotId: report.rawSnapshotId, displayedEvidenceSnapshotId: report.evidenceSnapshotId,
    },
    templateStatus: "complete_layout", ...overrides,
  };
}
