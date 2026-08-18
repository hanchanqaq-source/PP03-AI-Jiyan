import { evidenceFixtures } from "@/features/evidence-center/fixtures";
import type { MarketNewsEvent, MarketNewsQuery } from "./types";

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
  tag_evidence: [{ id: "semiconductor", name: "半导体", provenance: "article_text" }],
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
  importance_score: 400,
  verification_status: galaxyComputeCenter.status,
  verification_fixture: "frontend_demo",
};

const PROTOTYPE_REFERENCE_NOW = Date.parse("2026-08-18T23:59:59+08:00");
const RELATION_PRIORITY: Record<MarketNewsEvent["relation_level"], number> = {
  direct_holding: 3,
  industry_relation: 2,
  watch_tag: 1,
  none: 0,
};

function timestamp(event: MarketNewsEvent): number {
  return event.published_at_latest ? Date.parse(event.published_at_latest) : Number.NEGATIVE_INFINITY;
}

function matchesPrototypeQuery(query: MarketNewsQuery): boolean {
  if (query.mode !== "my_focus") return false;
  if (query.category !== "all" && query.category !== marketNewsPrototypeEvent.category) return false;
  const selectedTags = new Set(query.tag_ids);
  const hasSelectedArticleTag = marketNewsPrototypeEvent.tag_evidence.some((tag) => tag.provenance === "article_text" && selectedTags.has(tag.id));
  if (!hasSelectedArticleTag) return false;
  const publishedAt = timestamp(marketNewsPrototypeEvent);
  return Number.isFinite(publishedAt) && publishedAt >= PROTOTYPE_REFERENCE_NOW - query.days * 24 * 60 * 60 * 1000;
}

function compareForQuery(left: MarketNewsEvent, right: MarketNewsEvent, sort: MarketNewsQuery["sort"]): number {
  if (sort === "importance") return right.importance_score - left.importance_score || timestamp(right) - timestamp(left) || left.event_id.localeCompare(right.event_id);
  if (sort === "latest") return timestamp(right) - timestamp(left) || left.event_id.localeCompare(right.event_id);
  return RELATION_PRIORITY[right.relation_level] - RELATION_PRIORITY[left.relation_level]
    || right.importance_score - left.importance_score
    || timestamp(right) - timestamp(left)
    || left.event_id.localeCompare(right.event_id);
}

export function marketNewsDisplayEvents(events: MarketNewsEvent[], query: MarketNewsQuery): MarketNewsEvent[] {
  if (!matchesPrototypeQuery(query)) return [...events];
  return [...events, marketNewsPrototypeEvent].sort((left, right) => compareForQuery(left, right, query.sort));
}
