import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Plus, Search, X } from "lucide-react";
import { childrenOf, descendantsOf, rootTags, searchTags } from "./catalog";
import type { TagDefinition } from "./types";

interface TagSelectorProps {
  open: boolean;
  selectedIds: string[];
  onCancel: () => void;
  onConfirm: (ids: string[]) => void;
  onCreate?: (name: string) => unknown;
  customTags?: TagDefinition[];
}

function TagCheck({ tag, checked, onToggle, depth = 0 }: {
  tag: TagDefinition;
  checked: boolean;
  onToggle: (id: string) => void;
  depth?: number;
}) {
  return (
    <label className="flex min-h-11 cursor-pointer items-start gap-2 rounded-lg px-2 py-2 text-sm hover:bg-muted/50 focus-within:ring-2 focus-within:ring-primary/70"
      style={{ paddingLeft: `${8 + depth * 18}px` }}>
      <input type="checkbox" checked={checked} onChange={() => onToggle(tag.id)}
        aria-label={tag.name} className="mt-0.5 h-4 w-4 accent-orange-500" />
      <span className="min-w-0">
        <span className="font-medium text-foreground">{tag.name}</span>
        {tag.description && <span className="ml-2 text-xs text-muted-foreground">{tag.description}</span>}
      </span>
    </label>
  );
}

const FOCUSABLE = [
  "button:not([disabled])",
  "input:not([disabled])",
  "summary",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function TagSelector({
  open,
  selectedIds,
  onCancel,
  onConfirm,
  onCreate,
  customTags = [],
}: TagSelectorProps) {
  const [query, setQuery] = useState("");
  const [customName, setCustomName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [draft, setDraft] = useState<string[]>(selectedIds);
  const dialogRef = useRef<HTMLElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      setDraft([...selectedIds]);
      setQuery("");
      setCustomName("");
      setCreateError(null);
    }
  }, [open, selectedIds]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    searchRef.current?.focus();
    return () => {
      const previous = previousFocusRef.current;
      previousFocusRef.current = null;
      previous?.focus();
    };
  }, [open]);

  const matches = useMemo(() => {
    const builtIns = searchTags(query);
    const needle = query.trim().normalize("NFKC").toLocaleLowerCase("zh-CN");
    const customMatches = customTags.filter((tag) => (
      !needle || tag.name.normalize("NFKC").toLocaleLowerCase("zh-CN").includes(needle)
    ));
    return [...builtIns, ...customMatches.filter((tag) => !builtIns.some((item) => item.id === tag.id))];
  }, [customTags, query]);
  if (!open) return null;

  const toggle = (id: string) => setDraft((current) => (
    current.includes(id) ? current.filter((tagId) => tagId !== id) : [...current, id]
  ));

  const createCustom = async () => {
    if (!onCreate) return;
    try {
      await onCreate(customName);
      setCreateError(null);
      onCancel();
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "标签创建失败，请检查名称后重试");
    }
  };

  const trapFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) || []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onCancel();
    }}>
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="tag-selector-title"
        aria-describedby="tag-selector-description" onKeyDown={trapFocus}
        className="glass flex max-h-[82vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-primary/30 shadow-2xl motion-reduce:scroll-auto">
        <header className="flex items-start justify-between border-b border-border/60 px-5 py-4">
          <div>
            <h2 id="tag-selector-title" className="text-lg font-bold">添加投研标签</h2>
            <p id="tag-selector-description" className="mt-1 text-xs text-muted-foreground">按行业与产业链多选；市场资讯和行业研究分别保存。</p>
          </div>
          <button onClick={onCancel} aria-label="关闭标签选择器" className="min-h-11 min-w-11 rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            <X className="h-4 w-4" />
          </button>
        </header>

        {onCreate && <form className="border-b border-border/50 px-5 py-3" onSubmit={(event) => {
          event.preventDefault();
          void createCustom();
        }}>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="min-w-0 flex-1 text-xs text-muted-foreground">自定义行业名称
              <input aria-label="自定义行业名称" value={customName} onChange={(event) => {
                setCustomName(event.target.value);
                setCreateError(null);
              }} placeholder="1–32 个字符"
                className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-black/15 px-3 text-sm text-foreground outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/40" />
            </label>
            <button type="submit" aria-label="创建并激活" className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-primary/50 px-4 text-sm font-semibold text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              <Plus className="h-4 w-4" />创建并激活
            </button>
          </div>
          {createError && <p role="alert" className="mt-2 text-sm text-destructive">{createError}</p>}
          <p className="mt-1.5 text-xs text-muted-foreground">重名会复用已有标签；没有模板时只显示建设状态，不会自动补造数据。</p>
        </form>}

        <div className="border-b border-border/50 px-5 py-3">
          <label className="flex items-center gap-2 rounded-xl border border-border bg-black/15 px-3 py-2 focus-within:border-primary/60">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input ref={searchRef} aria-label="搜索标签" value={query} onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标签、别名或关键词" className="min-h-8 w-full bg-transparent text-sm outline-none" />
          </label>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {query ? (
            matches.length ? matches.map((tag) => (
              <TagCheck key={tag.id} tag={tag} checked={draft.includes(tag.id)} onToggle={toggle} />
            )) : <p className="py-12 text-center text-sm text-muted-foreground">没有匹配标签</p>
          ) : (
            <div className="space-y-3">
              {!!customTags.length && <section aria-label="自定义标签" className="rounded-xl border border-primary/25 bg-primary/5 p-2">
                <h3 className="px-2 pb-1 text-xs font-semibold text-primary">自定义标签</h3>
                {customTags.map((tag) => <TagCheck key={tag.id} tag={tag} checked={draft.includes(tag.id)} onToggle={toggle} />)}
              </section>}
              <div className="grid gap-3 md:grid-cols-2">
              {rootTags().map((root) => {
                const descendants = descendantsOf(root.id);
                return (
                  <details key={root.id} open={root.id === "technology"} className="rounded-xl border border-border/60 bg-black/10">
                    <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2.5">
                      <span className="font-semibold">{root.name}</span>
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        {descendants.length} <ChevronDown className="h-3.5 w-3.5" />
                      </span>
                    </summary>
                    <div className="border-t border-border/40 py-1">
                      <TagCheck tag={root} checked={draft.includes(root.id)} onToggle={toggle} />
                      {childrenOf(root.id).map((second) => (
                        <div key={second.id}>
                          <TagCheck tag={second} checked={draft.includes(second.id)} onToggle={toggle} depth={1} />
                          {childrenOf(second.id).map((third) => (
                            <TagCheck key={third.id} tag={third} checked={draft.includes(third.id)} onToggle={toggle} depth={2} />
                          ))}
                        </div>
                      ))}
                    </div>
                  </details>
                );
              })}
              </div>
            </div>
          )}
        </div>

        <footer className="flex items-center justify-between border-t border-border/60 px-5 py-4">
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Check className="h-4 w-4 text-primary" /> 已选 {draft.length} 个标签
          </span>
          <div className="flex gap-2">
            <button onClick={onCancel} className="min-h-11 rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">取消</button>
            <button onClick={() => onConfirm(draft)} className="min-h-11 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-glow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">确认添加</button>
          </div>
        </footer>
      </section>
    </div>
  );
}
