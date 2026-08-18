import { evidenceFixtures } from "@/features/evidence-center/fixtures";
import type { MarketNewsEvent } from "./types";

const galaxyComputeCenter = evidenceFixtures.find((fixture) => fixture.eventId === "galaxy-compute-center");

if (!galaxyComputeCenter) throw new Error("Missing galaxy-compute-center evidence fixture");

export const marketNewsPrototypeEvent: MarketNewsEvent = {
  event_id: galaxyComputeCenter.eventId,
  title: galaxyComputeCenter.title,
  summary: galaxyComputeCenter.claim,
  summary_status: "source_excerpt",
  category: "company",
  published_at_first: "2026-08-18T13:20:00+08:00",
  published_at_latest: "2026-08-18T13:20:00+08:00",
  sources: [],
  source_count: 0,
  related_tags: [{ id: "semiconductor", name: "半导体" }],
  tag_evidence: [],
  related_companies: [],
  related_funds: [],
  relation_level: "none",
  relation_evidence: [],
  impact_tendency: "unclear",
  impact_basis: ["前端演示 Fixture，不产生投资结论"],
  confidence: "unavailable",
  original_links: [],
  data_status: "cache",
  missing_information: ["前端演示 Fixture，不代表在线核验"],
  importance_score: galaxyComputeCenter.impact,
  verification_status: galaxyComputeCenter.status,
  verification_fixture: "frontend_demo",
};
