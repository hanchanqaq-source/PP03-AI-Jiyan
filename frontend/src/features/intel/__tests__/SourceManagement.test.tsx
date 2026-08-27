import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      radarSources: vi.fn(),
      testRadarSourceDefinition: vi.fn(),
      addRadarSource: vi.fn(),
      testRadarSource: vi.fn(),
      setRadarSourceEnabled: vi.fn(),
      deleteRadarSource: vi.fn(),
    },
  };
});

import { SourceManagement } from "@/features/intel/SourceManagement";
import { api, type RadarSourceListing } from "@/lib/api";


const listing: RadarSourceListing = {
  store_status: "ok",
  store_error: null,
  api_adapters: [],
  industries: [
    { key: "ai", name: "AI / 大模型", accent: "#fff" },
    { key: "semi", name: "半导体 / 芯片", accent: "#000" },
  ],
  summary: { total: 2, built_in: 1, custom: 1, enabled: 2 },
  sources: [
    {
      id: "builtin-1", source_type: "rss", built_in: true, enabled: true,
      name: "Builtin News", display_url: "https://builtin.example/rss?api_key=%5Bredacted%5D", hint: "ai", region: "GLOBAL",
      health: { status: "ok", checked_at: "2026-08-27T00:00:00Z" },
    },
    {
      id: "custom-1", source_type: "rss", built_in: false, enabled: true,
      name: "Custom News", display_url: "https://custom.example/rss", hint: "semi", region: "GLOBAL",
      health: { status: "failed", checked_at: "2026-08-27T00:00:00Z", error_message: "连接失败" },
    },
  ],
};


describe("SourceManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.radarSources).mockResolvedValue(listing);
    vi.mocked(api.testRadarSourceDefinition).mockResolvedValue({
      ok: true, http_status: 200, feed_format: "rss", item_count: 3,
      sample_title: "Public item", sample_link: "https://publisher.example/1",
      checked_at: "2026-08-27T00:00:00Z",
    });
    vi.mocked(api.addRadarSource).mockResolvedValue(listing.sources[1]);
    vi.mocked(api.testRadarSource).mockResolvedValue({ ok: true, source_id: "custom-1", http_status: 200 });
    vi.mocked(api.setRadarSourceEnabled).mockResolvedValue({ id: "custom-1", built_in: false, enabled: false });
    vi.mocked(api.deleteRadarSource).mockResolvedValue({ id: "custom-1", deleted: true });
  });

  it("shows built-in/custom identity, health, and never offers delete for built-ins", async () => {
    render(<SourceManagement />);
    expect(await screen.findByText("Builtin News")).toBeInTheDocument();
    expect(screen.getByText("Custom News")).toBeInTheDocument();
    expect(screen.getByText("内置")).toBeInTheDocument();
    expect(screen.getByText("自定义")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除 Custom News" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除 Builtin News" })).not.toBeInTheDocument();
    expect(screen.getByText("连接失败")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("stage-c-super-secret");
    expect(screen.getByText("https://builtin.example/rss?api_key=%5Bredacted%5D")).not.toHaveAttribute("href");
  });

  it("tests, disables, and deletes existing sources through native API", async () => {
    const user = userEvent.setup();
    render(<SourceManagement />);
    await screen.findByText("Custom News");

    await user.click(screen.getByRole("button", { name: "测试 Custom News" }));
    await user.click(screen.getByRole("button", { name: "停用 Custom News" }));
    await user.click(screen.getByRole("button", { name: "删除 Custom News" }));
    await user.click(screen.getByRole("button", { name: "确认删除 Custom News" }));

    expect(api.testRadarSource).toHaveBeenCalledWith("custom-1");
    expect(api.setRadarSourceEnabled).toHaveBeenCalledWith("custom-1", false);
    expect(api.deleteRadarSource).toHaveBeenCalledWith("custom-1");
  });

  it("requires a successful connection test before saving a custom RSS source", async () => {
    const user = userEvent.setup();
    render(<SourceManagement />);
    await screen.findByText("Builtin News");

    await user.type(screen.getByLabelText("来源名称"), "New Feed");
    await user.type(screen.getByLabelText("来源 URL"), "https://new.example/rss");
    await user.selectOptions(screen.getByLabelText("归属赛道"), "semi");
    expect(screen.getByRole("button", { name: "保存来源" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(await screen.findByText(/连接成功/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存来源" }));

    expect(api.testRadarSourceDefinition).toHaveBeenCalledWith(expect.objectContaining({
      source_type: "rss", name: "New Feed", url: "https://new.example/rss", hint: "semi",
    }));
    expect(api.addRadarSource).toHaveBeenCalled();
  });

  it("surfaces corrupt local storage as preserved and read-only", async () => {
    vi.mocked(api.radarSources).mockResolvedValueOnce({
      ...listing,
      store_status: "corrupt",
      store_error: "自定义配置已损坏；内置来源继续可用，写入已禁用",
    });
    render(<SourceManagement />);
    expect(await screen.findByText(/写入已禁用/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存来源" })).toBeDisabled();
  });
});
