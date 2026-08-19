import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CostBudgetPanel } from "@/features/source-catalog/CostBudgetPanel";
import type {
  AdapterConfigurationView,
  AdapterCostView,
  AdapterUsageView,
  BudgetGateState,
} from "@/features/source-catalog/types";

const configuration: AdapterConfigurationView = {
  adapter_id: "fmp",
  billing_model: "paid_api",
  catalog_status: "unconfigured",
  enabled: false,
  usage_mode: null,
  daily_budget: null,
  monthly_budget: null,
  per_request_budget: null,
  daily_request_limit: null,
  monthly_request_limit: null,
  credential: {
    configured: false,
    status: "unconfigured",
    last_validated_at: null,
    credential_source: "none",
  },
};

const unobservedUsage: AdapterUsageView = {
  adapter_id: "fmp",
  day: "2026-08-20",
  month: "2026-08",
  usage_status: "unobserved",
  daily_cost: null,
  monthly_cost: null,
  daily_request_count: null,
  monthly_request_count: null,
  daily_units: null,
  monthly_units: null,
  status_counts: {},
  open_reservations: null,
};

const unobservedCost: AdapterCostView = {
  adapter_id: "fmp",
  billing_model: "paid_api",
  enabled: false,
  credential_configured: false,
  status: "unconfigured",
  usage_status: "unobserved",
  day: "2026-08-20",
  month: "2026-08",
  daily_budget: null,
  monthly_budget: null,
  per_request_budget: null,
  daily_cost: null,
  monthly_cost: null,
  daily_remaining: null,
  monthly_remaining: null,
  open_reservations: null,
};

function Harness({
  usage = unobservedUsage,
  cost = unobservedCost,
  onSave = vi.fn().mockResolvedValue(undefined),
}: {
  usage?: AdapterUsageView;
  cost?: AdapterCostView;
  onSave?: (updates: { daily_budget: string; monthly_budget: string; per_request_budget: string }) => Promise<void>;
}) {
  const [gate, setGate] = useState<BudgetGateState>({ valid: false, saved: false });
  return <>
    <CostBudgetPanel
      configuration={configuration}
      usage={usage}
      cost={cost}
      onSave={onSave}
      onGateChange={setGate}
    />
    <output aria-label="预算门槛">{gate.valid ? "有效" : "无效"}/{gate.saved ? "已保存" : "未保存"}</output>
  </>;
}

describe("CostBudgetPanel", () => {
  it("keeps unobserved usage explicit instead of rendering factual zero", () => {
    render(<Harness />);
    expect(screen.getByText("用量尚未观测")).toBeInTheDocument();
    expect(screen.queryByText("¥0")).not.toBeInTheDocument();
    expect(screen.getByText(/2026-08-20/)).toBeInTheDocument();
    expect(screen.getByText(/2026-08/)).toBeInTheDocument();
  });

  it("renders an observed true zero with period metadata", () => {
    render(<Harness
      usage={{ ...unobservedUsage, usage_status: "observed", daily_cost: "0", monthly_cost: "0.00000000", daily_request_count: 0, monthly_request_count: 0, daily_units: "0", monthly_units: "0", open_reservations: 0 }}
      cost={{ ...unobservedCost, usage_status: "observed", daily_cost: "0", monthly_cost: "0.00000000", daily_remaining: null, monthly_remaining: null, open_reservations: 0 }}
    />);
    expect(screen.getAllByText("¥0")).toHaveLength(2);
    expect(screen.queryByText("用量尚未观测")).not.toBeInTheDocument();
  });

  it("rejects non-canonical, zero, and over-bound decimal budgets", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const daily = screen.getByRole("textbox", { name: "每日预算" });
    const monthly = screen.getByRole("textbox", { name: "每月预算" });
    const request = screen.getByRole("textbox", { name: "单次预算" });

    await user.type(daily, "01.00");
    await user.type(monthly, "1000000000000.00000001");
    await user.type(request, "0");

    expect(screen.getByRole("button", { name: "保存预算" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("请输入大于 0 的规范十进制金额");
    expect(screen.getByLabelText("预算门槛")).toHaveTextContent("无效/未保存");
  });

  it("saves exact decimal strings and marks edited values unsaved", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue(undefined);
    render(<Harness onSave={save} />);

    await user.type(screen.getByRole("textbox", { name: "每日预算" }), "1.25");
    await user.type(screen.getByRole("textbox", { name: "每月预算" }), "20.00000001");
    await user.type(screen.getByRole("textbox", { name: "单次预算" }), "0.01");
    expect(screen.getByLabelText("预算门槛")).toHaveTextContent("有效/未保存");

    await user.click(screen.getByRole("button", { name: "保存预算" }));
    expect(save).toHaveBeenCalledWith({
      daily_budget: "1.25",
      monthly_budget: "20.00000001",
      per_request_budget: "0.01",
    });
    expect(await screen.findByLabelText("预算门槛")).toHaveTextContent("有效/已保存");

    await user.clear(screen.getByRole("textbox", { name: "每日预算" }));
    await user.type(screen.getByRole("textbox", { name: "每日预算" }), "0");
    expect(screen.getByLabelText("预算门槛")).toHaveTextContent("无效/未保存");
  });
});
