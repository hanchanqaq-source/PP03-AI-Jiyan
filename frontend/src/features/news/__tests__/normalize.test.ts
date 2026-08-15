import type { RadarData } from "@/lib/api";
import { normalizeRadar } from "../normalize";

const now = Date.UTC(2026, 7, 16, 4, 0, 0) / 1000;
const radar: RadarData = {
  generated_at: "2026-08-16 12:00",
  recent_days: 7,
  stats: { industries: 2, total_sources: 4 },
  industries: [
    {
      key: "semi", name: "半导体 / 芯片", accent: "#22d3ee", total: 3,
      items: [
        { title: "HBM memory demand expands", url: "https://a/hbm", time: "08-16 10:00", ts: now - 60, source: "Source A", summary: "DRAM and HBM" },
        { title: "HBM: Memory demand expands!", url: "https://b/hbm", time: "08-16 09:58", ts: now - 120, source: "Source B", summary: "HBM" },
        { title: "国家发布半导体产业政策", url: "https://c/policy", time: "08-16 09:00", ts: now - 180, source: "政策发布", summary: "政策支持" },
        { title: "晶圆制造设备更新", url: "https://d/wafer", time: "08-10 09:00", ts: now - 6 * 86400, source: "Industry", summary: "设备" },
      ],
    },
    {
      key: "robot", name: "机器人 / 自动化", accent: "#14b8a6", total: 1,
      items: [{ title: "Humanoid robot enters production", url: "https://r/1", time: "08-16 08:00", ts: now - 240, source: "Robot Report", summary: "robotics" }],
    },
  ],
};

describe("tag-aware news normalization", () => {
  it("maps semiconductor to the whole semiconductor track", () => {
    expect(normalizeRadar(radar, { tagId: "semiconductor", days: 30, now }).map((event) => event.title))
      .toEqual(expect.arrayContaining(["国家发布半导体产业政策", "晶圆制造设备更新"]));
  });

  it("narrows storage to storage keywords and merges duplicate sources", () => {
    const events = normalizeRadar(radar, { tagId: "storage", days: 30, now });
    expect(events).toHaveLength(1);
    expect(events[0].sources).toEqual(["Source A", "Source B"]);
    expect(events[0].sentiment).toBe("影响不明确");
  });

  it("maps robotics to the robot track", () => {
    expect(normalizeRadar(radar, { tagId: "robotics", days: 30, now })[0].title)
      .toBe("Humanoid robot enters production");
  });

  it("applies the requested time window", () => {
    expect(normalizeRadar(radar, { tagId: "semiconductor", days: 3, now }).map((event) => event.title))
      .not.toContain("晶圆制造设备更新");
  });

  it("prioritizes holding-related and policy events without inventing sentiment", () => {
    const events = normalizeRadar(radar, {
      tagId: "semiconductor", days: 30, now, holdingTagIds: ["storage"],
    });
    expect(events[0].holdingRelated).toBe(true);
    expect(events[1].categories).toContain("政策");
    expect(events.every((event) => event.sentiment === "影响不明确")).toBe(true);
  });

  it("decodes RSS HTML entities as plain text", () => {
    const encoded: RadarData = {
      ...radar,
      industries: [{
        ...radar.industries[0],
        items: [{
          title: "Memory &amp; Storage",
          url: "https://a/entities",
          time: "08-16 10:00",
          ts: now - 60,
          source: "Source A",
          summary: "HBM &quot;roadmap&quot;",
        }],
      }],
    };

    expect(normalizeRadar(encoded, { tagId: "semiconductor", days: 30, now })[0])
      .toMatchObject({ title: "Memory & Storage", summary: 'HBM "roadmap"' });
  });
});
