import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, decodeIndustryResearchResponse } from "@/lib/api";


const reportWire = {
  industry_id: "storage",
  template_status: "complete_layout",
  trusted_snapshot_id: "trusted-storage-old",
  displayed_trusted_snapshot_id: "trusted-storage-old",
  raw_snapshot_id: "raw-storage-old",
  evidence_snapshot_id: "evidence-storage-old",
  generated_at: "2026-08-25T08:00:00+00:00",
  demo: false,
  source_coverage: {
    unit: "capability",
    total: 0,
    configured: 0,
    healthy: 0,
    partial_failure: 0,
    failed: 0,
    unconfigured: 0,
  },
  counts: { verified: 0, corroborated: 0 },
  overview: {
    conclusion_id: "conclusion-storage",
    industry_id: "storage",
    rule_version: "storage-cycle-v1",
    status: "unavailable",
    cycle_stage: null,
    outlook_direction: null,
    confidence_level: null,
    data_completeness: {
      verified_metric_count: 0,
      required_metric_count: 2,
      ratio: 0,
    },
    text: "规则=storage-cycle-v1；状态=unavailable；周期=暂无可靠数据；方向=暂无可靠数据；可信度=暂无可靠数据；完整度=0.00；证据=无",
    basis_metric_ids: [],
    evidence_ids: [],
    invalidating_conditions: [],
  },
  cycle: [],
  chain: [],
  metrics: [],
  capital: [],
  companies: [],
  fund_selection: [],
  funds: [],
  news_risk: [{
    industry_id: "storage",
    event_id: "trusted-event",
    status: "verified",
    occurred_at: "2026-08-24T08:00:00+00:00",
    evidence_ids: ["evidence-trusted"],
    roles: ["news"],
  }],
};

const candidateWire = {
  industry_id: "storage",
  candidate_snapshot_id: "candidate-refresh-1",
  counts: {
    unverified: 0,
    conflicting: 0,
    unverified_events: 1,
    conflicting_events: 0,
  },
  unverified: [],
  conflicting: [],
  unverified_events: [{
    industry_id: "storage",
    event_id: "candidate-event",
    status: "unverified",
    occurred_at: "2026-08-24T09:00:00+00:00",
    evidence_ids: ["evidence-candidate"],
    supporting_evidence_ids: ["evidence-candidate"],
    contradicting_evidence_ids: [],
    roles: ["catalyst"],
    candidate_snapshot_id: "candidate-refresh-1",
    raw_snapshot_id: "raw-refresh-1",
    evidence_snapshot_id: "evidence-refresh-1",
  }],
  conflicting_events: [],
  raw_snapshot_id: "raw-refresh-1",
  evidence_snapshot_id: "evidence-refresh-1",
  external_lineages: [],
};

const responseWire = {
  requested_industry_id: "storage",
  displayed_industry_id: "storage",
  displayed_trusted_report: reportWire,
  candidate_evidence: candidateWire,
  refresh_run: {
    industry_id: "storage",
    run_id: "refresh-1",
    raw_snapshot_id: "raw-refresh-1",
    evidence_snapshot_id: "evidence-refresh-1",
    candidate_snapshot_id: "candidate-refresh-1",
    phase: "verifying",
    error_code: null,
    displayed_trusted_snapshot_id: "trusted-storage-old",
    published_trusted_snapshot_id: null,
    displayed_raw_snapshot_id: "raw-storage-old",
    displayed_evidence_snapshot_id: "evidence-storage-old",
  },
  template_status: "complete_layout",
};

function trustedMetricWire(overrides: Record<string, unknown> = {}) {
  return {
    industry_id: "storage",
    metric_id: "dram_price",
    label: "DRAM 价格",
    current_value: 1,
    unit: "index",
    change: { value: 1, basis: "wow" },
    historical_position: { value: 60, window: "3y", method: "percentile" },
    availability_status: "available",
    verification_status: "verified",
    freshness_status: "fresh",
    source_run_status: "healthy",
    empty_reason: null,
    as_of_date: "2026-08-24",
    fetched_at: "2026-08-25T08:00:00+00:00",
    methodology: "Official disclosed index.",
    judgment_basis: ["Official evidence supports DRAM price."],
    invalidating_conditions: ["Disclosure is corrected."],
    evidence: [{
      evidence_id: "evidence-dram",
      source_family_id: "family-official",
      content_source: "official.example",
      origin_cluster: "origin-official",
      collector_source: "collector-official",
      final_url: "https://official.example/dram",
      is_official: true,
      is_official_attested: true,
      supports_claim: true,
      supports_fields: ["dram_price"],
      contradicts_claim: false,
      as_of_date: "2026-08-24",
      verified_at: "2026-08-25T08:00:00+00:00",
    }],
    independent_source_families: ["family-official"],
    independent_content_sources: ["official.example"],
    independent_origin_clusters: ["origin-official"],
    raw_snapshot_id: "raw-storage-old",
    evidence_snapshot_id: "evidence-storage-old",
    expires_at: null,
    ...overrides,
  };
}

function responseWithTrustedMetric(metric: Record<string, unknown> = trustedMetricWire()) {
  const value = structuredClone(responseWire) as any;
  value.displayed_trusted_report.cycle = [metric];
  value.displayed_trusted_report.counts = { verified: 1, corroborated: 0 };
  value.displayed_trusted_report.overview = {
    ...value.displayed_trusted_report.overview,
    status: "partial",
    data_completeness: {
      verified_metric_count: 1,
      required_metric_count: 2,
      ratio: 0.5,
    },
    text: "规则=storage-cycle-v1；状态=partial；周期=暂无可靠数据；方向=暂无可靠数据；可信度=暂无可靠数据；完整度=0.50；证据=evidence-dram",
    basis_metric_ids: ["dram_price"],
    evidence_ids: ["evidence-dram"],
    invalidating_conditions: ["Disclosure is corrected."],
  };
  return value;
}

function corroboratedMetricWire() {
  const value = trustedMetricWire({ verification_status: "corroborated" });
  value.evidence = [
    {
      ...value.evidence[0],
      is_official: false,
      is_official_attested: false,
    },
    {
      ...value.evidence[0],
      evidence_id: "evidence-dram-two",
      source_family_id: "family-two",
      content_source: "second.example",
      origin_cluster: "origin-two",
      collector_source: "collector-two",
      final_url: "https://second.example/dram",
      is_official: false,
      is_official_attested: false,
    },
  ];
  value.independent_source_families = ["family-official", "family-two"];
  value.independent_content_sources = ["official.example", "second.example"];
  value.independent_origin_clusters = ["origin-official", "origin-two"];
  return value;
}

function responseWithConflictValues(left: string, right: string) {
  const value = structuredClone(responseWire) as any;
  value.candidate_evidence.counts.conflicting = 1;
  value.candidate_evidence.conflicting = [{
    industry_id: "storage",
    metric_id: "manufacturer_capex",
    aggregate_value: null,
    source_values: [
      {
        evidence_id: "conflict-a",
        source_family_id: "family-a",
        value: left,
        unit: "index",
        as_of_date: "2026-08-24",
        change: null,
      },
      {
        evidence_id: "conflict-b",
        source_family_id: "family-b",
        value: right,
        unit: "index",
        as_of_date: "2026-08-24",
        change: null,
      },
    ],
    raw_snapshot_id: "raw-refresh-1",
    evidence_snapshot_id: "evidence-refresh-1",
  }];
  return value;
}

function jsonResponse(value: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("industry research API decoder", () => {
  it("maps an exact snake_case wire response to the camelCase page model", () => {
    const decoded = decodeIndustryResearchResponse(responseWire);

    expect(decoded).toMatchObject({
      requestedIndustryId: "storage",
      displayedIndustryId: "storage",
      templateStatus: "complete_layout",
      displayedTrustedReport: {
        industryId: "storage",
        trustedSnapshotId: "trusted-storage-old",
        sourceCoverage: { partialFailure: 0 },
        overview: { cycleStage: null, dataCompleteness: { requiredMetricCount: 2 } },
      },
      refreshRun: {
        candidateSnapshotId: "candidate-refresh-1",
        displayedTrustedSnapshotId: "trusted-storage-old",
      },
    });
  });

  it("keeps candidate events in their typed side channel and never promotes them", () => {
    const decoded = decodeIndustryResearchResponse(responseWire);

    expect(decoded.displayedTrustedReport?.newsRisk.map((event) => event.eventId)).toEqual([
      "trusted-event",
    ]);
    expect(decoded.candidateEvidence?.unverifiedEvents.map((event) => event.eventId)).toEqual([
      "candidate-event",
    ]);
    expect(decoded.displayedTrustedReport?.newsRisk).not.toContainEqual(
      expect.objectContaining({ eventId: "candidate-event" }),
    );
  });

  it("rejects a candidate event with an arbitrary foreign non-A2 lineage", () => {
    const value = structuredClone(responseWire) as any;
    value.candidate_evidence.unverified_events[0].raw_snapshot_id = "raw-foreign";

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("accepts a canonical A2 news event with independent raw lineage", () => {
    const value = structuredClone(responseWire) as any;
    value.candidate_evidence.unverified_events[0].candidate_snapshot_id = "news-evidence-1";
    value.candidate_evidence.unverified_events[0].raw_snapshot_id = "news-raw-1";
    value.candidate_evidence.unverified_events[0].evidence_snapshot_id = "news-evidence-1";
    value.candidate_evidence.external_lineages = [{
      kind: "a2_news",
      candidate_snapshot_id: "news-evidence-1",
      raw_snapshot_id: "news-raw-1",
      evidence_snapshot_id: "news-evidence-1",
    }];

    expect(() => decodeIndustryResearchResponse(value)).not.toThrow();
  });

  it("rejects a tampered declared A2 lineage", () => {
    const value = structuredClone(responseWire) as any;
    value.candidate_evidence.unverified_events[0].candidate_snapshot_id = "news-evidence-1";
    value.candidate_evidence.unverified_events[0].raw_snapshot_id = "news-raw-1";
    value.candidate_evidence.unverified_events[0].evidence_snapshot_id = "news-evidence-1";
    value.candidate_evidence.external_lineages = [{
      kind: "a2_news",
      candidate_snapshot_id: "news-evidence-1",
      raw_snapshot_id: "tampered-raw",
      evidence_snapshot_id: "news-evidence-1",
    }];

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("accepts a backend-admitted conflict whose display values differ only by case", () => {
    const value = structuredClone(responseWire) as any;
    value.candidate_evidence.counts.conflicting = 1;
    value.candidate_evidence.conflicting = [{
      industry_id: "storage",
      metric_id: "manufacturer_capex",
      aggregate_value: null,
      source_values: [
        {
          evidence_id: "conflict-a",
          source_family_id: "family-a",
          value: "UP",
          unit: "INDEX",
          as_of_date: "2026-08-24",
          change: null,
        },
        {
          evidence_id: "conflict-b",
          source_family_id: "family-b",
          value: "up",
          unit: "index",
          as_of_date: "2026-08-24",
          change: null,
        },
      ],
      raw_snapshot_id: "raw-refresh-1",
      evidence_snapshot_id: "evidence-refresh-1",
    }];

    expect(() => decodeIndustryResearchResponse(value)).not.toThrow();
  });

  it.each([
    ["German sharp s", "STRASSE", "STRAßE"],
    ["Greek final sigma", "ΟΣ", "οσ"],
    ["compatibility ligature", "office", "oﬃce"],
    ["Cyrillic historic form", "ᲀ", "в"],
    ["Greek combining ypogegrammeni", "ͅ", "ι"],
  ])("accepts a canonical backend conflict even when display values casefold equally: %s", (_label, left, right) => {
    expect(() => decodeIndustryResearchResponse(responseWithConflictValues(left, right)))
      .not.toThrow();
  });

  it("accepts the canonical A2 same-display-value contradiction wire contract", () => {
    expect(() => decodeIndustryResearchResponse(responseWithConflictValues("12.5", "12.5")))
      .not.toThrow();
  });

  it.each([
    ["fullwidth Latin letter", "Ａ", "A"],
    ["superscript digit", "¹", "1"],
  ])("does not apply compatibility normalization to a real conflict: %s", (_label, left, right) => {
    expect(() => decodeIndustryResearchResponse(responseWithConflictValues(left, right)))
      .not.toThrow();
  });

  it.each([
    ["missing field", () => {
      const value = structuredClone(responseWire) as Record<string, unknown>;
      delete value.refresh_run;
      return value;
    }],
    ["unknown enum", () => ({
      ...structuredClone(responseWire),
      refresh_run: { ...responseWire.refresh_run, phase: "queued_by_ai" },
    })],
    ["camelCase wire key", () => {
      const value = structuredClone(responseWire) as Record<string, unknown>;
      value.displayedIndustryId = value.displayed_industry_id;
      delete value.displayed_industry_id;
      return value;
    }],
    ["unknown field", () => ({ ...structuredClone(responseWire), ai_summary: "fabricated" })],
  ])("fails closed on %s", (_label, fixture) => {
    expect(() => decodeIndustryResearchResponse(fixture())).toThrow(ApiError);
  });

  it("rejects an unverified candidate inserted into trusted news", () => {
    const value = structuredClone(responseWire);
    value.displayed_trusted_report.news_risk[0].status = "unverified";

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it.each([
    ["unverified non-null metric", () => {
      const metric = trustedMetricWire({ verification_status: "unverified" });
      const value = responseWithTrustedMetric(metric);
      value.displayed_trusted_report.counts = { verified: 0, corroborated: 0 };
      return value;
    }],
    ["verified metric without evidence", () => responseWithTrustedMetric(
      trustedMetricWire({ evidence: [] }),
    )],
    ["verified metric without official attestation", () => {
      const metric = trustedMetricWire();
      metric.evidence[0].is_official_attested = false;
      return responseWithTrustedMetric(metric);
    }],
    ["trusted metric with contradicting evidence", () => {
      const metric = trustedMetricWire();
      metric.evidence[0].contradicts_claim = true;
      return responseWithTrustedMetric(metric);
    }],
    ["expired trusted metric", () => responseWithTrustedMetric(
      trustedMetricWire({ freshness_status: "expired" }),
    )],
    ["trusted metric without methodology", () => responseWithTrustedMetric(
      trustedMetricWire({ methodology: "" }),
    )],
  ])("rejects %s from a trusted report", (_label, fixture) => {
    expect(() => decodeIndustryResearchResponse(fixture())).toThrow(ApiError);
  });

  it.each([
    ["source families", "independent_source_families"],
    ["content sources", "independent_content_sources"],
    ["origin clusters", "independent_origin_clusters"],
  ])("requires two matching independent %s for corroboration", (_label, field) => {
    const metric = corroboratedMetricWire() as Record<string, unknown>;
    metric[field] = [(metric[field] as string[])[0]];
    const value = responseWithTrustedMetric(metric);
    value.displayed_trusted_report.counts = { verified: 0, corroborated: 1 };

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it.each([
    ["chain", () => {
      const value = structuredClone(responseWire) as any;
      value.displayed_trusted_report.chain = [{
        industry_id: "robotics",
        node_id: "foreign-node",
        label: "Foreign",
        observation_ids: [],
        evidence_ids: [],
        status: "unavailable",
      }];
      return value;
    }],
    ["company", () => {
      const value = structuredClone(responseWire) as any;
      value.displayed_trusted_report.companies = [{
        industry_id: "robotics",
        security_code: "900001",
        company_name: "Fixture company",
        chain_node_id: "foreign-node",
        relation_type: "official_disclosure",
        key_metric_ids: [],
        evidence_ids: ["company-evidence"],
        as_of_date: "2026-08-24",
        observation_only: true,
      }];
      return value;
    }],
    ["fund relation", () => {
      const value = structuredClone(responseWire) as any;
      value.displayed_trusted_report.fund_selection = [{
        selection_id: "selection-1", fund_code: "900001", selected_in_request: true,
      }];
      value.displayed_trusted_report.funds = [{
        selection_id: "selection-1",
        fund_code: "900001",
        relation: {
          industry_id: "robotics",
          fund_code: "900001",
          relation_layer: "official_allocation",
          exposure_value: 10,
          exposure_unit: "percent",
          disclosure_date: "2026-08-24",
          evidence_ids: ["fund-evidence"],
          status: "verified",
        },
        empty_reason: null,
      }];
      return value;
    }],
  ])("rejects cross-industry %s rows", (_label, fixture) => {
    expect(() => decodeIndustryResearchResponse(fixture())).toThrow(ApiError);
  });

  it("rejects report counts that do not match decoded trusted metrics", () => {
    const value = structuredClone(responseWire);
    value.displayed_trusted_report.counts.verified = 1;

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("accepts a verified metric only when its complete Task 1 trust proof is present", () => {
    expect(decodeIndustryResearchResponse(responseWithTrustedMetric())).toMatchObject({
      displayedTrustedReport: {
        counts: { verified: 1, corroborated: 0 },
        overview: { dataCompleteness: { verifiedMetricCount: 1, requiredMetricCount: 2 } },
      },
    });
  });

  it.each(["fresh", "stale"])(
    "rejects a %s trusted value whose structured expiry has passed",
    (freshnessStatus) => {
      const value = responseWithTrustedMetric(trustedMetricWire({
        freshness_status: freshnessStatus,
        expires_at: "2026-08-25T07:59:59+00:00",
      }));

      expect(() => decodeIndustryResearchResponse(
        value,
        new Date("2026-08-25T08:00:00+00:00"),
      )).toThrow(ApiError);
    },
  );

  it("accepts a trusted value whose structured expiry is after the injected decode clock", () => {
    const value = responseWithTrustedMetric(trustedMetricWire({
      freshness_status: "stale",
      expires_at: "2026-08-25T08:00:01+00:00",
    }));

    expect(decodeIndustryResearchResponse(
      value,
      new Date("2026-08-25T08:00:00+00:00"),
    )).toMatchObject({ displayedTrustedReport: { counts: { verified: 1 } } });
  });

  it("does not let a post-import Date.parse patch admit a malformed timestamp", () => {
    vi.spyOn(Date, "parse").mockReturnValue(0);
    const value = structuredClone(responseWire) as any;
    value.displayed_trusted_report.generated_at = "not-a-date+00:00";

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("does not let a post-import Date.parse patch bypass structured expiry", () => {
    vi.spyOn(Date, "parse").mockReturnValue(new Date("2099-01-01T00:00:00+00:00").getTime());
    const value = responseWithTrustedMetric(trustedMetricWire({
      freshness_status: "fresh",
      expires_at: "2026-08-25T07:59:59+00:00",
    }));

    expect(() => decodeIndustryResearchResponse(
      value,
      new Date("2026-08-25T08:00:00+00:00"),
    )).toThrow(ApiError);
  });

  it("rejects completeness ratios that do not match their exact counts", () => {
    const value = structuredClone(responseWire);
    value.displayed_trusted_report.overview.data_completeness.ratio = 0.5;

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("rejects completeness counts not supported by decoded trusted rows", () => {
    const value = structuredClone(responseWire);
    value.displayed_trusted_report.overview.data_completeness = {
      verified_metric_count: 1,
      required_metric_count: 2,
      ratio: 0.5,
    };

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("rejects completeness that undercounts decoded required trusted rows", () => {
    const value = responseWithTrustedMetric();
    value.displayed_trusted_report.overview = {
      ...value.displayed_trusted_report.overview,
      status: "unavailable",
      data_completeness: {
        verified_metric_count: 0,
        required_metric_count: 2,
        ratio: 0,
      },
      text: "规则=storage-cycle-v1；状态=unavailable；周期=暂无可靠数据；方向=暂无可靠数据；可信度=暂无可靠数据；完整度=0.00；证据=无",
      basis_metric_ids: [],
      evidence_ids: [],
      invalidating_conditions: [],
    };

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it.each([
    ["published phase without lineage", () => {
      const value = structuredClone(responseWire) as any;
      value.displayed_industry_id = null;
      value.displayed_trusted_report = null;
      value.candidate_evidence = null;
      value.refresh_run = {
        ...value.refresh_run,
        run_id: null,
        raw_snapshot_id: null,
        evidence_snapshot_id: null,
        candidate_snapshot_id: null,
        phase: "trusted_published",
        displayed_trusted_snapshot_id: null,
        published_trusted_snapshot_id: null,
        displayed_raw_snapshot_id: null,
        displayed_evidence_snapshot_id: null,
      };
      return value;
    }],
    ["published ID outside published phase", () => ({
      ...structuredClone(responseWire),
      refresh_run: {
        ...responseWire.refresh_run,
        phase: "idle",
        published_trusted_snapshot_id: "trusted-storage-old",
      },
    })],
    ["displayed raw lineage without displayed snapshot", () => {
      const value = structuredClone(responseWire) as any;
      value.displayed_industry_id = null;
      value.displayed_trusted_report = null;
      value.refresh_run.displayed_trusted_snapshot_id = null;
      return value;
    }],
  ])("rejects refresh invariant: %s", (_label, fixture) => {
    expect(() => decodeIndustryResearchResponse(fixture())).toThrow(ApiError);
  });

  it("rejects a displayed report for an industry other than the requested industry", () => {
    const value = structuredClone(responseWire) as any;
    value.requested_industry_id = "semiconductor";
    value.refresh_run.industry_id = "semiconductor";
    value.candidate_evidence = null;
    value.refresh_run.candidate_snapshot_id = null;

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("rejects displayed refresh lineage when the report and displayed industry are absent", () => {
    const value = structuredClone(responseWire) as any;
    value.displayed_industry_id = null;
    value.displayed_trusted_report = null;

    expect(() => decodeIndustryResearchResponse(value)).toThrow(ApiError);
  });

  it("accepts a nonblank bounded company security code that is not a fund code", () => {
    const value = structuredClone(responseWire) as any;
    value.displayed_trusted_report.companies = [{
      industry_id: "storage",
      security_code: "NVDA",
      company_name: "NVIDIA",
      chain_node_id: "end_applications",
      relation_type: "public_classification",
      key_metric_ids: [],
      evidence_ids: ["company-evidence"],
      as_of_date: "2026-08-24",
      observation_only: true,
    }];

    expect(decodeIndustryResearchResponse(value)).toMatchObject({
      displayedTrustedReport: { companies: [{ securityCode: "NVDA" }] },
    });
  });

  it("decodes the unknown-tag building state without fabricating a report", () => {
    const value = {
      requested_industry_id: "custom-advanced-packaging",
      displayed_industry_id: null,
      displayed_trusted_report: null,
      candidate_evidence: null,
      refresh_run: {
        industry_id: "custom-advanced-packaging",
        run_id: null,
        raw_snapshot_id: null,
        evidence_snapshot_id: null,
        candidate_snapshot_id: null,
        phase: "idle",
        error_code: null,
        displayed_trusted_snapshot_id: null,
        published_trusted_snapshot_id: null,
        displayed_raw_snapshot_id: null,
        displayed_evidence_snapshot_id: null,
      },
      template_status: "building",
    };

    expect(decodeIndustryResearchResponse(value)).toEqual(expect.objectContaining({
      requestedIndustryId: "custom-advanced-packaging",
      displayedIndustryId: null,
      displayedTrustedReport: null,
      candidateEvidence: null,
      templateStatus: "building",
    }));
  });
});

describe("industry research API client", () => {
  it("GET encodes only the industry id and approved window", async () => {
    const transport = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(responseWire));

    await expect(api.industryResearchReport("storage", 30)).resolves.toMatchObject({
      requestedIndustryId: "storage",
    });

    expect(transport).toHaveBeenCalledWith(
      "/api/industry-research/storage?window_days=30",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("sends explicit fund codes only in a no-store POST body", async () => {
    const projection = {
      state: "resolved",
      fund_selection: [{
        selection_id: "selection-1",
        fund_code: "900001",
        selected_in_request: true,
      }],
      resolutions: [{
        selection_id: "selection-1",
        fund_code: "900001",
        relation: null,
        empty_reason: "unknown",
      }],
      pending_lookthrough_selection_ids: [],
    };
    const transport = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(projection, { "Cache-Control": "no-store" }),
    );

    await api.resolveIndustryFundRelations("storage", ["900001"]);

    const [url, options] = transport.mock.calls[0];
    expect(url).toBe("/api/industry-research/storage/fund-relations/resolve");
    expect(String(url)).not.toContain("900001");
    expect(options).toEqual(expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ fund_codes: ["900001"] }),
      headers: expect.objectContaining({ "X-PP03-Write-Intent": "1" }),
    }));
  });

  it("requires the no_holdings state for an empty requested fund set", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({
      state: "resolved",
      fund_selection: [],
      resolutions: [],
      pending_lookthrough_selection_ids: [],
    }));

    await expect(api.resolveIndustryFundRelations("storage", []))
      .rejects.toBeInstanceOf(ApiError);
  });

  it("rejects invalid industry ids and windows before transport", async () => {
    const transport = vi.spyOn(globalThis, "fetch");

    await expect(api.industryResearchReport("../storage", 30)).rejects.toBeInstanceOf(ApiError);
    await expect(api.industryResearchReport("storage", 14 as 7)).rejects.toBeInstanceOf(ApiError);
    expect(transport).not.toHaveBeenCalled();
  });

  it.each([
    ["duplicate selections", () => ({
      state: "resolved",
      fund_selection: [
        { selection_id: "selection-1", fund_code: "900001", selected_in_request: true },
        { selection_id: "selection-1", fund_code: "900002", selected_in_request: true },
      ],
      resolutions: [
        { selection_id: "selection-1", fund_code: "900001", relation: null, empty_reason: "unknown" },
        { selection_id: "selection-1", fund_code: "900002", relation: null, empty_reason: "unknown" },
      ],
      pending_lookthrough_selection_ids: [],
    })],
    ["mismatched resolution identity", () => ({
      state: "resolved",
      fund_selection: [
        { selection_id: "selection-1", fund_code: "900001", selected_in_request: true },
      ],
      resolutions: [
        { selection_id: "selection-2", fund_code: "900002", relation: null, empty_reason: "unknown" },
      ],
      pending_lookthrough_selection_ids: [],
    })],
    ["unknown pending selection", () => ({
      state: "resolved",
      fund_selection: [
        { selection_id: "selection-1", fund_code: "900001", selected_in_request: true },
      ],
      resolutions: [
        { selection_id: "selection-1", fund_code: "900001", relation: null, empty_reason: "unknown" },
      ],
      pending_lookthrough_selection_ids: ["selection-missing"],
    })],
    ["cross-industry resolved relation", () => ({
      state: "resolved",
      fund_selection: [
        { selection_id: "selection-1", fund_code: "900001", selected_in_request: true },
      ],
      resolutions: [{
        selection_id: "selection-1",
        fund_code: "900001",
        relation: {
          industry_id: "robotics",
          fund_code: "900001",
          relation_layer: "official_allocation",
          exposure_value: 10,
          exposure_unit: "percent",
          disclosure_date: "2026-08-24",
          evidence_ids: ["fund-evidence"],
          status: "verified",
        },
        empty_reason: null,
      }],
      pending_lookthrough_selection_ids: [],
    })],
  ])("fails closed on fund projection invariant: %s", async (_label, fixture) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(fixture()));

    await expect(api.resolveIndustryFundRelations("storage", ["900001"]))
      .rejects.toBeInstanceOf(ApiError);
  });

  it.each([
    ["missing requested code", {
      state: "resolved",
      fund_selection: [
        { selection_id: "selection-1", fund_code: "900001", selected_in_request: true },
      ],
      resolutions: [
        { selection_id: "selection-1", fund_code: "900001", relation: null, empty_reason: "unknown" },
      ],
      pending_lookthrough_selection_ids: [],
    }],
    ["unrequested code", {
      state: "resolved",
      fund_selection: [
        { selection_id: "selection-1", fund_code: "900001", selected_in_request: true },
        { selection_id: "selection-2", fund_code: "900003", selected_in_request: true },
      ],
      resolutions: [
        { selection_id: "selection-1", fund_code: "900001", relation: null, empty_reason: "unknown" },
        { selection_id: "selection-2", fund_code: "900003", relation: null, empty_reason: "unknown" },
      ],
      pending_lookthrough_selection_ids: [],
    }],
  ])("rejects a fund response with %s", async (_label, projection) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(projection));

    await expect(api.resolveIndustryFundRelations("storage", ["900001", "900001", "900002"]))
      .rejects.toBeInstanceOf(ApiError);
  });
});
