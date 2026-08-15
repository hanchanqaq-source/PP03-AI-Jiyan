import { INCOMPLETE_REPORT_MESSAGE, getIndustryTemplate } from "../templates";

describe("industry report templates", () => {
  it("uses storage-specific metrics", () => {
    const labels = getIndustryTemplate("storage")!.metrics.map((metric) => metric.label);
    expect(labels).toEqual(expect.arrayContaining(["DRAM价格", "NAND价格", "HBM需求", "库存水平", "厂商资本开支"]));
  });

  it("uses semiconductor-specific chain nodes", () => {
    const nodes = getIndustryTemplate("semiconductor")!.chain.map((node) => node.name);
    expect(nodes).toEqual(expect.arrayContaining(["设备与材料", "芯片设计", "晶圆制造", "封装测试", "终端需求"]));
  });

  it("uses robotics-specific metrics", () => {
    const labels = getIndustryTemplate("robotics")!.metrics.map((metric) => metric.label);
    expect(labels).toEqual(expect.arrayContaining(["产量", "销量", "订单", "减速器", "伺服系统", "国产化率"]));
  });

  it("never marks template-only numeric data as verified", () => {
    for (const id of ["storage", "semiconductor", "robotics"]) {
      expect(getIndustryTemplate(id)!.metrics.every((metric) => metric.status !== "verified")).toBe(true);
    }
  });

  it("returns the explicit incomplete-template state for other tags", () => {
    expect(getIndustryTemplate("healthcare")).toBeUndefined();
    expect(INCOMPLETE_REPORT_MESSAGE).toBe("该行业报告模板正在完善");
  });
});
