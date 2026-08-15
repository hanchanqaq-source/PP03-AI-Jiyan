import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Search, X } from "lucide-react";
import { childrenOf, descendantsOf, rootTags, searchTags } from "./catalog";
import type { TagDefinition } from "./types";

interface TagSelectorProps {
  open: boolean;
  selectedIds: string[];
  onCancel: () => void;
  onConfirm: (ids: string[]) => void;
}

function TagCheck({ tag, checked, onToggle, depth = 0 }: {
  tag: TagDefinition;
  checked: boolean;
  onToggle: (id: string) => void;
  depth?: number;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-muted/50"
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

export function TagSelector({ open, selectedIds, onCancel, onConfirm }: TagSelectorProps) {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<string[]>(selectedIds);

  useEffect(() => {
    if (open) {
      setDraft([...selectedIds]);
      setQuery("");
    }
  }, [open, selectedIds]);

  const matches = useMemo(() => searchTags(query), [query]);
  if (!open) return null;

  const toggle = (id: string) => setDraft((current) => (
    current.includes(id) ? current.filter((tagId) => tagId !== id) : [...current, id]
  ));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onCancel();
    }}>
      <section role="dialog" aria-modal="true" aria-labelledby="tag-selector-title"
        className="glass flex max-h-[82vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-primary/30 shadow-2xl">
        <header className="flex items-start justify-between border-b border-border/60 px-5 py-4">
          <div>
            <h2 id="tag-selector-title" className="text-lg font-bold">添加投研标签</h2>
            <p className="mt-1 text-xs text-muted-foreground">按行业与产业链多选；市场资讯和行业研究分别保存。</p>
          </div>
          <button onClick={onCancel} aria-label="关闭标签选择器" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="border-b border-border/50 px-5 py-3">
          <label className="flex items-center gap-2 rounded-xl border border-border bg-black/15 px-3 py-2 focus-within:border-primary/60">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input value={query} onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标签、别名或关键词" className="w-full bg-transparent text-sm outline-none" autoFocus />
          </label>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {query ? (
            matches.length ? matches.map((tag) => (
              <TagCheck key={tag.id} tag={tag} checked={draft.includes(tag.id)} onToggle={toggle} />
            )) : <p className="py-12 text-center text-sm text-muted-foreground">没有匹配标签</p>
          ) : (
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
          )}
        </div>

        <footer className="flex items-center justify-between border-t border-border/60 px-5 py-4">
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Check className="h-4 w-4 text-primary" /> 已选 {draft.length} 个标签
          </span>
          <div className="flex gap-2">
            <button onClick={onCancel} className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground">取消</button>
            <button onClick={() => onConfirm(draft)} className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-glow">确认添加</button>
          </div>
        </footer>
      </section>
    </div>
  );
}
