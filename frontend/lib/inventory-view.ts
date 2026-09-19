import type { InventoryItem, ItemCandidate } from "./api";

export type InventoryGroup = {
  key: string;
  label: string;
  items: InventoryItem[];
  earliestExpiry: string | null;
};

export function normalizeInventoryLabel(label: string): string {
  return label.trim().toLocaleLowerCase("zh-TW");
}

export function groupInventoryItems(items: InventoryItem[]): InventoryGroup[] {
  const groups = new Map<string, InventoryGroup>();
  for (const item of items) {
    const key = normalizeInventoryLabel(item.label);
    const group = groups.get(key);
    if (!group) {
      groups.set(key, {
        key,
        label: item.label.trim(),
        items: [item],
        earliestExpiry: item.expires_on,
      });
      continue;
    }
    group.items.push(item);
    if (item.expires_on && (!group.earliestExpiry || item.expires_on < group.earliestExpiry)) {
      group.earliestExpiry = item.expires_on;
    }
  }
  return [...groups.values()].sort((left, right) => left.label.localeCompare(right.label, "zh-TW"));
}

export function takeChoiceTitle(item: ItemCandidate): string {
  return `${item.label} · ${item.expires_on ?? "未填期限"}`;
}

export function takeChoiceOwner(item: ItemCandidate): string {
  return item.owner_display_name ?? item.owner_name ?? "未知";
}

export function inventorySharingLabel(item: InventoryItem): string {
  if (item.shared) return "全體共用";
  if (item.access_type === "SHARED_DIRECT") return "指定共用";
  return "私人";
}

export function canEditInventoryItem(item: InventoryItem): boolean {
  return item.can_edit;
}

export async function saveInventoryEdit(
  update: () => Promise<unknown>,
  refresh: () => Promise<unknown>,
): Promise<void> {
  await update();
  await refresh();
}
