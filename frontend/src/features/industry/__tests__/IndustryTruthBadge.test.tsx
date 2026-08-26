import { render, screen } from "@testing-library/react";
import { VerificationBadge } from "../IndustryTruthBadge";

describe("industry evidence state semantics", () => {
  it("keeps evidence states out of green market-move semantics", () => {
    const { container } = render(<div>
      <span data-testid="verified"><VerificationBadge status="verified" /></span>
      <span data-testid="corroborated"><VerificationBadge status="corroborated" /></span>
      <span data-testid="unverified"><VerificationBadge status="unverified" /></span>
      <span data-testid="conflicting"><VerificationBadge status="conflicting" /></span>
      <span data-testid="unavailable"><VerificationBadge status="unavailable" /></span>
    </div>);
    expect(screen.getByTestId("verified").firstElementChild).toHaveClass("text-primary");
    expect(screen.getByTestId("corroborated").firstElementChild?.className).toMatch(/text-purple-/);
    expect(screen.getByTestId("unverified").firstElementChild).toHaveClass("text-warning");
    expect(screen.getByTestId("conflicting").firstElementChild).toHaveClass("text-destructive");
    expect(screen.getByTestId("unavailable").firstElementChild).toHaveClass("text-muted-foreground");
    expect(container.querySelectorAll(".text-success")).toHaveLength(0);
    expect(screen.getByTestId("verified")).toHaveTextContent("已核验");
    expect(screen.getByTestId("corroborated")).toHaveTextContent("多源印证");
    expect(screen.getByTestId("unverified")).toHaveTextContent("待核验");
    expect(screen.getByTestId("conflicting")).toHaveTextContent("发生冲突");
    expect(screen.getByTestId("unavailable")).toHaveTextContent("暂无可靠数据");
  });
});
