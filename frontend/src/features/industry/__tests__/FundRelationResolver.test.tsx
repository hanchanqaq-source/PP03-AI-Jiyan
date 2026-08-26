import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api } from "@/lib/api";
import { FundRelationResolver } from "../FundRelationResolver";

describe("explicit transient fund relation resolver", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("does not request until submit and sends only the current deduplicated selection", async () => {
    const user = userEvent.setup();
    const resolve = vi.spyOn(api, "resolveIndustryFundRelations").mockResolvedValue({
      state: "resolved",
      fundSelection: [
        { selectionId: "selection-000001", fundCode: "000001", selectedInRequest: true },
        { selectionId: "selection-000002", fundCode: "000002", selectedInRequest: true },
      ],
      resolutions: [
        { selectionId: "selection-000001", fundCode: "000001", relation: { industryId: "storage", fundCode: "000001", relationLayer: "official_allocation", exposureValue: null, exposureUnit: null, disclosureDate: "2026-06-30", evidenceIds: ["E-FUND-1"], status: "verified" }, emptyReason: null },
        { selectionId: "selection-000002", fundCode: "000002", relation: { industryId: "storage", fundCode: "000002", relationLayer: "disclosed_lookthrough", exposureValue: 12.3, exposureUnit: "percent", disclosureDate: "2026-06-30", evidenceIds: ["E-FUND-2A", "E-FUND-2B"], status: "corroborated" }, emptyReason: null },
      ],
      pendingLookthroughSelectionIds: ["selection-000001"],
    });
    render(<FundRelationResolver industryId="storage" />);

    expect(resolve).not.toHaveBeenCalled();
    expect(screen.getByText("还没有基金持仓")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000001, 000002, 000001");
    expect(resolve).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));

    await waitFor(() => expect(resolve).toHaveBeenCalledWith("storage", ["000001", "000002"], expect.any(AbortSignal)));
    expect(screen.getByRole("heading", { name: "官方行业配置" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "披露持仓穿透" })).toBeInTheDocument();
    expect(screen.getByText("待穿透，不等同于当前行业暴露")).toBeInTheDocument();
    expect(screen.getByText(/多源印证/)).toBeInTheDocument();
    expect(JSON.stringify(localStorage)).not.toContain("000001");
    expect(document.body).not.toHaveTextContent(/金额|成本|账号|排行榜|买入建议/);
  });

  it("returns to an accurate non-factual empty state after failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "resolveIndustryFundRelations").mockRejectedValue(new Error("offline"));
    render(<FundRelationResolver industryId="storage" />);
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000003");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("基金关系来源暂不可用");
    expect(screen.getByText("暂无可靠数据")).toBeInTheDocument();
    expect(screen.queryByText("0%")) .not.toBeInTheDocument();
  });

  it("aborts an in-flight request when the industry changes and never renders stale relations", async () => {
    const user = userEvent.setup();
    const resolve = vi.spyOn(api, "resolveIndustryFundRelations").mockImplementation((_industry, _codes, signal) => new Promise((_done, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const { rerender } = render(<FundRelationResolver industryId="storage" />);
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000004");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    const signal = resolve.mock.calls[0][2];
    rerender(<FundRelationResolver industryId="robotics" />);

    expect(signal?.aborted).toBe(true);
    expect(screen.getByRole("textbox", { name: "基金代码" })).toHaveValue("");
    expect(screen.queryByText("000004")).not.toBeInTheDocument();
  });
});
