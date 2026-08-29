import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Settings } from "@/pages/Settings";

const subscriptionApi = vi.hoisted(() => ({
  loadSubscriptionProviders: vi.fn(),
  startCodexLogin: vi.fn(),
  testCodexConnection: vi.fn(),
  cancelCodexTest: vi.fn(),
}));

vi.mock("@/lib/subscription-ai", () => subscriptionApi);
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const provider = (overrides: Record<string, unknown> = {}) => ({
  provider_id: "codex",
  installed: true,
  version: "1.2.3",
  auth_status: "installed_not_logged_in",
  available: false,
  message: "Codex 已安装，但尚未登录 ChatGPT。",
  test_status: "not_tested",
  last_test_at: null,
  ...overrides,
});

const providerResponse = (status = provider()) => [status];

describe("Settings subscription AI bridge", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse());
    subscriptionApi.startCodexLogin.mockResolvedValue(provider({ message: "等待官方登录完成。" }));
    subscriptionApi.cancelCodexTest.mockResolvedValue(provider({
      test_status: "cancelled",
      message: "测试已停止。",
      last_test_at: "2026-08-29T00:00:00Z",
    }));
  });

  it("opens the membership/free-quota mode by default", async () => {
    render(<Settings />);

    expect(screen.getByText("优先使用已经购买的会员或本地 AI，不必另外填写 API Key")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /会员 \/ 免费额度接入/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("OpenAI API Key")).not.toBeInTheDocument();
    expect(await screen.findByText("Codex")).toBeInTheDocument();
    expect(screen.getByText("使用你的 ChatGPT/Codex 套餐额度；额度和限制由 OpenAI 官方管理。")).toBeInTheDocument();
    expect(screen.getByText("当前能力：分析页面已经提供的数据")).toBeInTheDocument();
    expect(screen.getByText("测试连接会消耗极少量 Codex 会员额度。")).toBeInTheDocument();
    expect(screen.queryByText("Claude Code")).not.toBeInTheDocument();
    expect(screen.queryByText(/Plus|Pro|剩余额度|已用额度/)).not.toBeInTheDocument();
  });

  it("shows an honest loading state before detection resolves", () => {
    subscriptionApi.loadSubscriptionProviders.mockReturnValue(new Promise(() => undefined));
    render(<Settings />);

    expect(screen.getByText("正在检查本机 Codex 状态…")).toBeInTheDocument();
  });

  it("shows uninstalled and not-logged-in states without claiming availability", async () => {
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      installed: false,
      version: null,
      auth_status: "not_installed",
      message: "未检测到 Codex CLI。",
    })));
    const { unmount } = render(<Settings />);

    expect(await screen.findByText("未安装")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开 Codex 官方登录" })).toBeDisabled();

    unmount();
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse());
    render(<Settings />);
    expect(await screen.findByText("未登录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测试连接" })).toBeDisabled();
    expect(screen.getByText("set CODEX_HOME=%VR_DATA_DIR%\\codex-home")).toBeInTheDocument();
    expect(screen.getByText("codex login")).toBeInTheDocument();
  });

  it("does not present API-key auth as ChatGPT membership", async () => {
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      auth_status: "logged_in_api_key",
      message: "Codex 当前使用 API Key；这不是 ChatGPT 会员态。",
    })));
    render(<Settings />);

    expect(await screen.findByText("API Key 登录")).toBeInTheDocument();
    expect(screen.getByText("当前 Codex 使用 API Key，不属于会员额度接入。")).toBeInTheDocument();
  });

  it("starts visible ChatGPT login and requests a fresh status", async () => {
    const user = userEvent.setup();
    subscriptionApi.startCodexLogin.mockResolvedValue(provider({ message: "等待官方登录完成。" }));
    subscriptionApi.loadSubscriptionProviders
      .mockResolvedValueOnce(providerResponse())
      .mockResolvedValue(providerResponse(provider({
        auth_status: "logged_in_chatgpt",
        available: true,
      })));
    render(<Settings />);

    await user.click(await screen.findByRole("button", { name: "打开 Codex 官方登录" }));

    expect(subscriptionApi.startCodexLogin).toHaveBeenCalledWith(false);
    await waitFor(() => expect(subscriptionApi.loadSubscriptionProviders).toHaveBeenCalledWith(true));
  });

  it("keeps polling after login starts until product-home ChatGPT auth appears", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      subscriptionApi.loadSubscriptionProviders
        .mockResolvedValueOnce(providerResponse())
        .mockResolvedValueOnce(providerResponse())
        .mockResolvedValue(providerResponse(provider({
          auth_status: "logged_in_chatgpt",
          available: true,
        })));
      render(<Settings />);

      await user.click(await screen.findByRole("button", { name: "打开 Codex 官方登录" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100);
      });

      expect(subscriptionApi.loadSubscriptionProviders.mock.calls.filter(([force]) => force === true).length).toBeGreaterThanOrEqual(2);
      expect(await screen.findByText("已通过 ChatGPT 登录")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("confirms before replacing an API-key login", async () => {
    const user = userEvent.setup();
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      auth_status: "logged_in_api_key",
    })));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Settings />);

    await user.click(await screen.findByRole("button", { name: "切换为 ChatGPT 登录" }));

    expect(confirm).toHaveBeenCalled();
    expect(subscriptionApi.startCodexLogin).toHaveBeenCalledWith(true);
  });

  it("tests one minimal Codex response and only then enables setting the default", async () => {
    const user = userEvent.setup();
    const ready = provider({
      auth_status: "logged_in_chatgpt",
      available: true,
    });
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(ready));
    subscriptionApi.testCodexConnection.mockResolvedValue(provider({
      auth_status: "logged_in_chatgpt",
      available: true,
      test_status: "success",
      message: "连接成功。",
      last_test_at: "2026-08-29T00:00:00Z",
    }));
    const { unmount } = render(<Settings />);

    const defaultButton = await screen.findByRole("button", { name: "设为默认 AI" });
    expect(defaultButton).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    expect(subscriptionApi.testCodexConnection).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("连接成功。")).toBeInTheDocument();
    expect(defaultButton).toBeEnabled();

    await user.click(defaultButton);
    expect(JSON.parse(localStorage.getItem("vr-llm") || "null")).toEqual({
      provider: "cli-codex",
      baseURL: "",
      apiKey: "",
      model: "codex",
    });

    unmount();
    render(<Settings />);
    expect(await screen.findByText("当前默认")).toBeInTheDocument();
  });

  it("does not enable the default after authentication becomes unavailable", async () => {
    localStorage.setItem("vr-llm", JSON.stringify({
      provider: "deepseek",
      baseURL: "https://example.invalid",
      apiKey: "existing-key",
      model: "deepseek-chat",
    }));
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      auth_status: "logged_in_api_key",
      available: false,
      test_status: "success",
    })));

    render(<Settings />);

    expect(await screen.findByRole("button", { name: "设为默认 AI" })).toBeDisabled();
  });

  it("offers a stop action while a connection test is running", async () => {
    const user = userEvent.setup();
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      auth_status: "logged_in_chatgpt",
      available: true,
    })));
    subscriptionApi.testCodexConnection.mockReturnValue(new Promise(() => undefined));
    render(<Settings />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "停止测试" }));

    expect(subscriptionApi.cancelCodexTest).toHaveBeenCalledTimes(1);
  });

  it("surfaces quota errors without suggesting a paid API fallback", async () => {
    const user = userEvent.setup();
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      auth_status: "logged_in_chatgpt",
      available: true,
    })));
    subscriptionApi.testCodexConnection.mockResolvedValue(provider({
      auth_status: "logged_in_chatgpt",
      available: true,
      test_status: "quota_or_rate_limited",
      message: "ChatGPT 会员额度暂不可用；未回退到按量计费 API。",
      last_test_at: "2026-08-29T00:00:00Z",
    }));
    render(<Settings />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("ChatGPT 会员额度暂不可用；未回退到按量计费 API。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设为默认 AI" })).toBeDisabled();
  });

  it("does not overwrite an existing AI config when the Codex test fails", async () => {
    const user = userEvent.setup();
    const current = {
      provider: "deepseek",
      baseURL: "https://api.deepseek.com",
      apiKey: "existing-key",
      model: "deepseek-chat",
    };
    localStorage.setItem("vr-llm", JSON.stringify(current));
    subscriptionApi.loadSubscriptionProviders.mockResolvedValue(providerResponse(provider({
      auth_status: "logged_in_chatgpt",
      available: true,
    })));
    subscriptionApi.testCodexConnection.mockResolvedValue(provider({
      auth_status: "logged_in_chatgpt",
      available: true,
      test_status: "process_failed",
      message: "连接失败，当前配置保持不变。",
    }));
    render(<Settings />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));

    expect(await screen.findByText("连接失败，当前配置保持不变。")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("vr-llm") || "null")).toEqual(current);
    expect(screen.getByRole("button", { name: "设为默认 AI" })).toBeDisabled();
  });

  it("keeps API configuration as a deliberate advanced option", async () => {
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(screen.getByRole("button", { name: /API 高级接入/ }));

    expect(screen.getByText("API 可能按量计费；系统不会自动从会员接入切换到 API。")).toBeInTheDocument();
    expect(screen.getByLabelText("OpenAI API Key")).toBeInTheDocument();
    expect(screen.getByLabelText("后端访问密钥（可选）")).toBeInTheDocument();
  });
});
