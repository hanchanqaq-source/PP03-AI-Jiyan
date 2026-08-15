import type { RadarData, RadarItem } from "@/lib/api";
import { getTag } from "@/features/tags/catalog";

export type NewsCategory = "全球" | "国内" | "政策" | "产业" | "公司" | "基金相关";
export type ImpactTendency = "偏正面" | "偏负面" | "中性" | "影响不明确";

export interface NormalizedNewsEvent {
  id: string;
  title: string;
  url: string;
  publishedAt: string;
  timestamp: number;
  sources: string[];
  tags: string[];
  categories: NewsCategory[];
  summary: string;
  impactedIndustries: string[];
  impactedCompanies: string[];
  holdingRelated: boolean;
  sentiment: ImpactTendency;
  priority: number;
}

export interface NormalizeNewsOptions {
  tagId: string;
  days: 1 | 3 | 7 | 30;
  category?: "全部" | NewsCategory;
  holdingTagIds?: string[];
  now?: number;
}

const POLICY_RE = /政策|监管|国务院|部委|补贴|法案|禁令|制裁|policy|regulation|government/i;
const COMPANY_RE = /公司|公告|财报|业绩|订单|量产|收购|并购|earnings|revenue|company|announc/i;
const FUND_RE = /基金|ETF|份额|fund/i;
const DOMESTIC_RE = /[\u3400-\u9fff]/;

function decodeHtmlEntities(value: string): string {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = value;
  return textarea.value;
}

function normalizeTitle(title: string): string {
  return title.toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");
}

function timestampOf(item: RadarItem, generatedAt: string | null): number {
  if (typeof item.ts === "number" && Number.isFinite(item.ts)) return item.ts;
  const year = generatedAt?.slice(0, 4) || new Date().getFullYear().toString();
  const parsed = Date.parse(`${year}-${item.time.replace(" ", "T")}:00+08:00`);
  return Number.isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
}

function categoriesOf(item: RadarItem): NewsCategory[] {
  const blob = `${item.title} ${item.summary || ""} ${item.source}`;
  const categories: NewsCategory[] = [DOMESTIC_RE.test(blob) ? "国内" : "全球", "产业"];
  if (POLICY_RE.test(blob)) categories.push("政策");
  if (COMPANY_RE.test(blob)) categories.push("公司");
  if (FUND_RE.test(blob)) categories.push("基金相关");
  return Array.from(new Set(categories));
}

function matchesTag(item: RadarItem, tagId: string): boolean {
  if (tagId !== "storage") return true;
  const tag = getTag(tagId);
  const blob = `${item.title} ${item.summary || ""} ${item.zh || ""}`.toLocaleLowerCase();
  return !!tag?.keywords.some((keyword) => blob.includes(keyword.toLocaleLowerCase()));
}

export function normalizeRadar(data: RadarData, options: NormalizeNewsOptions): NormalizedNewsEvent[] {
  const tag = getTag(options.tagId);
  if (!tag) return [];
  const tracks = tag.news_track
    ? data.industries.filter((industry) => industry.key === tag.news_track)
    : data.industries;
  const now = options.now ?? Math.floor(Date.now() / 1000);
  const cutoff = now - options.days * 86400;
  const grouped = new Map<string, NormalizedNewsEvent>();

  for (const track of tracks) {
    for (const item of track.items) {
      if (!matchesTag(item, tag.id)) continue;
      const timestamp = timestampOf(item, data.generated_at);
      if (timestamp && timestamp < cutoff) continue;
      const categories = categoriesOf(item);
      if (options.category && options.category !== "全部" && !categories.includes(options.category)) continue;
      const holdingRelated = (options.holdingTagIds || []).some((holdingId) => {
        const holdingTag = getTag(holdingId);
        return holdingTag?.news_track === track.key && matchesTag(item, holdingId);
      });
      const title = decodeHtmlEntities(item.zh || item.title);
      const summary = decodeHtmlEntities(item.summary || "暂无 AI 摘要；请查看原始来源。");
      const id = normalizeTitle(title);
      const priority = (holdingRelated ? 40 : 0) + (categories.includes("政策") ? 30 : 0)
        + (categories.includes("公司") ? 15 : 0) + Math.max(0, 10 - Math.floor((now - timestamp) / 86400));
      const existing = grouped.get(id);
      if (existing) {
        if (!existing.sources.includes(item.source)) existing.sources.push(item.source);
        if (timestamp > existing.timestamp) {
          existing.timestamp = timestamp;
          existing.publishedAt = item.time;
          existing.url = item.url || existing.url;
        }
        existing.holdingRelated = existing.holdingRelated || holdingRelated;
        existing.priority = Math.max(existing.priority, priority);
        continue;
      }
      grouped.set(id, {
        id,
        title,
        url: item.url,
        publishedAt: item.time,
        timestamp,
        sources: [item.source],
        tags: [tag.name],
        categories,
        summary,
        impactedIndustries: [tag.name],
        impactedCompanies: [],
        holdingRelated,
        sentiment: "影响不明确",
        priority,
      });
    }
  }

  return Array.from(grouped.values()).sort((a, b) => b.priority - a.priority || b.timestamp - a.timestamp);
}
