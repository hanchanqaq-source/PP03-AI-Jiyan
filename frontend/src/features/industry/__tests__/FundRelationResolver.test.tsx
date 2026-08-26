import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { FundRelationResolver } from "../FundRelationResolver";
import { jsonResponse } from "./fixtures";

function projectionWire(codes = ["000001", "000002"]) {
  return {
    state: codes.length ? "resolved" : "no_holdings",
    fund_selection: codes.map((code) => ({ selection_id: `selection-${code}`, fund_code: code, selected_in_request: true })),
    resolutions: codes.map((code, index) => ({
      selection_id: `selection-${code}`, fund_code: code,
      relation: {
        industry_id: "storage", fund_code: code,
        relation_layer: index === 0 ? "official_allocation" : "disclosed_lookthrough",
        exposure_value: index === 0 ? null : 12.3,
        exposure_unit: index === 0 ? null : "percent",
        disclosure_date: "2026-06-30",
        evidence_ids: index === 0 ? ["E-FUND-1"] : ["E-FUND-2A", "E-FUND-2B"],
        status: index === 0 ? "verified" : "corroborated",
      }, empty_reason: null,
    })),
    pending_lookthrough_selection_ids: codes.length ? [`selection-${codes[0]}`] : [],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("explicit transient fund relation resolver", () => {
  beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

  it("does not request until submit and sends only the current deduplicated selection through the real decoder", async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(projectionWire()));
    render(<FundRelationResolver industryId="storage" />);
    expect(request).not.toHaveBeenCalled();
    expect(screen.getByText("还没有基金持仓")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000001, 000002, 000001");
    expect(request).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual({ fund_codes: ["000001", "000002"] });
    expect(screen.getByText("已提交代码：000001、000002")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "官方行业配置" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "披露持仓穿透" })).toBeInTheDocument();
    expect(screen.getByText("待穿透，不等同于当前行业暴露")).toBeInTheDocument();
    expect(screen.getByText(/多源印证/)).toBeInTheDocument();
    expect(JSON.stringify(localStorage)).not.toContain("000001");
  });

  it("clears a resolved projection as soon as the input differs from the submitted selection", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(projectionWire(["000001"])));
    render(<FundRelationResolver industryId="storage" />);
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000001");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    expect(await screen.findByText("已提交代码：000001")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "2");
    expect(screen.queryByText("已提交代码：000001")).not.toBeInTheDocument();
    expect(screen.getByText("输入已变化，请重新提交本次选择")).toBeInTheDocument();
  });

  it("validates code format and the 32-code limit before networking with distinct messages", async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(globalThis, "fetch");
    const view = render(<FundRelationResolver industryId="storage" />);
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "123");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    expect(screen.getByRole("alert")).toHaveTextContent("基金代码格式无效");
    expect(request).not.toHaveBeenCalled();

    view.rerender(<FundRelationResolver industryId="robotics" />);
    const codes = Array.from({ length: 33 }, (_, index) => String(index).padStart(6, "0")).join(",");
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), codes);
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    expect(screen.getByRole("alert")).toHaveTextContent("本次最多提交 32 个基金代码");
    expect(request).not.toHaveBeenCalled();
  });

  it("distinguishes source unavailable from validation failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    render(<FundRelationResolver industryId="storage" />);
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000003");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("基金关系来源暂不可用");
    expect(screen.getByText("暂无可靠数据")).toBeInTheDocument();
    expect(screen.getByRole("alert")).not.toHaveTextContent("格式无效");
  });

  it("aborts the previous generation and ignores its late resolution", async () => {
    const user = userEvent.setup();
    const first = deferred<Response>();
    const second = deferred<Response>();
    const request = vi.spyOn(globalThis, "fetch").mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    render(<FundRelationResolver industryId="storage" />);
    const input = screen.getByRole("textbox", { name: "基金代码" });
    await user.type(input, "000004");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    await user.clear(input); await user.type(input, "000005");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    expect(request.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await act(async () => second.resolve(jsonResponse(projectionWire(["000005"]))));
    expect(await screen.findByText("已提交代码：000005")).toBeInTheDocument();
    await act(async () => first.resolve(jsonResponse(projectionWire(["000004"]))));
    expect(screen.getByText("已提交代码：000005")).toBeInTheDocument();
    expect(screen.queryByText("已提交代码：000004")).not.toBeInTheDocument();
  });

  it("aborts on unmount and never updates state from finally", async () => {
    const user = userEvent.setup();
    const pending = deferred<Response>();
    const request = vi.spyOn(globalThis, "fetch").mockImplementation(() => pending.promise);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const view = render(<FundRelationResolver industryId="storage" />);
    await user.type(screen.getByRole("textbox", { name: "基金代码" }), "000006");
    await user.click(screen.getByRole("button", { name: "解析本次选择" }));
    view.unmount();
    expect(request.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await act(async () => pending.resolve(jsonResponse(projectionWire(["000006"]))));
    expect(consoleError).not.toHaveBeenCalled();
  });
});
