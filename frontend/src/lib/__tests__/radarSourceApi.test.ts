import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";


describe("radar source API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("adds the local write-intent header to all source mutations", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    const definition = { source_type: "rss" as const, name: "Feed", url: "https://x.example/rss", hint: "ai" };

    await api.testRadarSourceDefinition(definition);
    await api.addRadarSource(definition);
    await api.testRadarSource("abc");
    await api.setRadarSourceEnabled("abc", false);
    await api.deleteRadarSource("abc");

    for (const [, options] of fetchMock.mock.calls) {
      expect(new Headers(options?.headers).get("X-PP03-Write-Intent")).toBe("1");
    }
  });
});
