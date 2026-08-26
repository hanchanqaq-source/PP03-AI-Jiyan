import { INCOMPLETE_REPORT_MESSAGE, getIndustryTemplate } from "../templates";

describe("industry report templates", () => {
  it("uses storage-specific metrics", () => {
    const labels = getIndustryTemplate("storage")!.metricLabels;
    expect(labels).toEqual(expect.arrayContaining(["DRAM 价格", "NAND 价格", "HBM 需求", "库存水平", "厂商资本开支"]));
  });

  it("uses semiconductor-specific chain nodes", () => {
    const nodes = getIndustryTemplate("semiconductor")!.chainLabels;
    expect(nodes).toEqual(expect.arrayContaining(["设备与材料", "芯片设计", "晶圆制造", "封装测试", "终端需求"]));
  });

  it("uses robotics-specific metrics", () => {
    const labels = getIndustryTemplate("robotics")!.metricLabels;
    expect(labels).toEqual(expect.arrayContaining(["样机进展", "订单", "交付", "量产进度", "减速器", "伺服系统"]));
  });

  it("keeps templates as labels only, without numeric data or truth claims", () => {
    for (const id of ["storage", "semiconductor", "robotics"]) {
      expect(JSON.stringify(getIndustryTemplate(id))).not.toMatch(/currentValue|verified/);
    }
  });

  it("returns the explicit incomplete-template state for other tags", () => {
    expect(getIndustryTemplate("healthcare")).toBeUndefined();
    expect(INCOMPLETE_REPORT_MESSAGE).toContain("报告模板建设中");
  });
});
