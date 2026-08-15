import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { TagSelector } from "../TagSelector";

describe("TagSelector", () => {
  it("searches, multi-selects and confirms without mutating the input selection", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<TagSelector open selectedIds={["storage"]} onCancel={vi.fn()} onConfirm={onConfirm} />);

    await user.type(screen.getByPlaceholderText("搜索标签、别名或关键词"), "机器人");
    const checkbox = screen.getByRole("checkbox", { name: "机器人" });
    expect(checkbox).not.toBeChecked();
    await user.click(checkbox);
    expect(screen.getByText("已选 2 个标签")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认添加" }));

    expect(onConfirm).toHaveBeenCalledWith(["storage", "robotics"]);
  });

  it("cancels without confirming draft changes", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(<TagSelector open selectedIds={[]} onCancel={onCancel} onConfirm={onConfirm} />);

    await user.click(screen.getByRole("checkbox", { name: "半导体" }));
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
