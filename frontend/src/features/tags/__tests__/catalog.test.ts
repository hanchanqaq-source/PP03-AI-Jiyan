import { childrenOf, getTag, rootTags, searchTags } from "../catalog";

describe("shared tag catalog", () => {
  it("finds the storage tag through a domain alias", () => {
    expect(searchTags("DRAM").map((tag) => tag.id)).toContain("storage");
  });

  it("keeps semiconductor, storage and robotics on distinct report templates", () => {
    const templates = ["semiconductor", "storage", "robotics"].map(
      (id) => getTag(id)?.report_template,
    );

    expect(templates).toEqual(["semiconductor", "storage", "robotics"]);
  });

  it("exposes the required extensible first-level industries", () => {
    expect(rootTags().map((tag) => tag.name)).toEqual(expect.arrayContaining([
      "科技", "医疗", "消费", "金融", "新能源", "高端制造", "军工",
      "周期资源", "传媒", "农业", "公用事业", "房地产", "交通运输", "海外市场",
    ]));
  });

  it("preserves the hierarchy below technology", () => {
    expect(childrenOf("technology").map((tag) => tag.id)).toEqual(expect.arrayContaining([
      "semiconductor", "artificial-intelligence", "robotics",
    ]));
    expect(childrenOf("semiconductor").map((tag) => tag.id)).toContain("storage");
  });
});
