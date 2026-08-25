import { useState } from "react";
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

  it("offers a keyboard-accessible custom create control and closes after immediate activation", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(<TagSelector open selectedIds={[]} onCancel={onCancel} onConfirm={vi.fn()} onCreate={onCreate} />);

    await user.type(screen.getByRole("textbox", { name: "自定义行业名称" }), "  先进封装  ");
    await user.click(screen.getByRole("button", { name: "创建并激活" }));

    expect(onCreate).toHaveBeenCalledWith("  先进封装  ");
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("shows a clear Chinese validation error without closing the dialog", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<TagSelector open selectedIds={[]} onCancel={onCancel} onConfirm={vi.fn()}
      onCreate={() => { throw new Error("标签名称不能超过 32 个字符"); }} />);

    await user.type(screen.getByRole("textbox", { name: "自定义行业名称" }), "研".repeat(33));
    await user.click(screen.getByRole("button", { name: "创建并激活" }));

    expect(screen.getByRole("alert")).toHaveTextContent("标签名称不能超过 32 个字符");
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("traps focus, cancels with Escape and restores focus to the opener", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return <>
        <button onClick={() => setOpen(true)}>打开标签选择器</button>
        <TagSelector open={open} selectedIds={[]} onCancel={() => setOpen(false)} onConfirm={vi.fn()} onCreate={vi.fn()} />
      </>;
    }

    render(<Harness />);
    const opener = screen.getByRole("button", { name: "打开标签选择器" });
    await user.click(opener);
    expect(screen.getByPlaceholderText("搜索标签、别名或关键词")).toHaveFocus();

    const close = screen.getByRole("button", { name: "关闭标签选择器" });
    close.focus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(screen.getByRole("button", { name: "确认添加" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
