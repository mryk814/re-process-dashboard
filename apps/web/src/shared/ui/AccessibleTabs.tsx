import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useState,
} from "react";

export type AccessibleTabItem<T extends string> = {
  id: T;
  label: ReactNode;
};

export function AccessibleTabList<T extends string>({
  idPrefix,
  label,
  items,
  selected,
  onSelect,
  className,
  activation = "automatic",
}: {
  idPrefix: string;
  label: string;
  items: readonly AccessibleTabItem<T>[];
  selected: T;
  onSelect: (id: T) => void;
  className?: string;
  activation?: "automatic" | "manual";
}) {
  const [focused, setFocused] = useState(selected);

  useEffect(() => {
    setFocused(selected);
  }, [selected]);

  const moveFocus = (
    event: KeyboardEvent<HTMLButtonElement>,
    nextIndex: number,
  ) => {
    event.preventDefault();
    const next = items[nextIndex];
    if (!next) return;
    setFocused(next.id);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`#${idPrefix}-tab-${next.id}`)
      ?.focus();
    if (activation === "automatic") onSelect(next.id);
  };

  return <div
    className={className}
    role="tablist"
    aria-label={label}
    aria-orientation="horizontal"
  >
    {items.map((item, index) => <button
      key={item.id}
      id={`${idPrefix}-tab-${item.id}`}
      type="button"
      role="tab"
      aria-selected={item.id === selected}
      aria-controls={`${idPrefix}-panel-${item.id}`}
      tabIndex={item.id === focused ? 0 : -1}
      className={item.id === selected ? "active" : ""}
      onFocus={() => setFocused(item.id)}
      onClick={() => onSelect(item.id)}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight") {
          moveFocus(event, (index + 1) % items.length);
        } else if (event.key === "ArrowLeft") {
          moveFocus(event, (index - 1 + items.length) % items.length);
        } else if (event.key === "Home") {
          moveFocus(event, 0);
        } else if (event.key === "End") {
          moveFocus(event, items.length - 1);
        } else if (
          activation === "manual"
          && (event.key === "Enter" || event.key === " ")
        ) {
          event.preventDefault();
          onSelect(item.id);
        }
      }}
    >{item.label}</button>)}
  </div>;
}

export function AccessibleTabPanel({
  idPrefix,
  tabId,
  active,
  children,
  className,
}: {
  idPrefix: string;
  tabId: string;
  active: boolean;
  children?: ReactNode;
  className?: string;
}) {
  return <div
    id={`${idPrefix}-panel-${tabId}`}
    role="tabpanel"
    aria-labelledby={`${idPrefix}-tab-${tabId}`}
    hidden={!active}
    className={className}
  >{children}</div>;
}
