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
    rule_version: "storage-v1",
    status: "unavailable",
    cycle_stage: null,
    outlook_direction: null,
    confidence_level: null,
    data_completeness: {
      verified_metric_count: 0,
      required_metric_count: 8,
      ratio: 0,
    },
    text: "暂无可靠数据",
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
        overview: { cycleStage: null, dataCompleteness: { requiredMetricCount: 8 } },
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

  it("rejects invalid industry ids and windows before transport", async () => {
    const transport = vi.spyOn(globalThis, "fetch");

    await expect(api.industryResearchReport("../storage", 30)).rejects.toBeInstanceOf(ApiError);
    await expect(api.industryResearchReport("storage", 14 as 7)).rejects.toBeInstanceOf(ApiError);
    expect(transport).not.toHaveBeenCalled();
  });
});
