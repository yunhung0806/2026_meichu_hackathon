import assert from "node:assert/strict";
import test from "node:test";

import { describeStationPageFailure, StationApiError } from "../lib/api.ts";
import type { InventoryItem } from "../lib/api.ts";
import { canEditInventoryItem, groupInventoryItems, inventorySharingLabel, isSharedInventoryItem, saveInventoryEdit, takeChoiceOwner, takeChoiceTitle } from "../lib/inventory-view.ts";

function item(overrides: Partial<InventoryItem>): InventoryItem {
  return {
    item_id: "item-default",
    label: "麥香",
    owner_id: "owner-a",
    owner_display_name: "A",
    shared: false,
    access_type: "OWNER",
    can_edit: true,
    can_take: true,
    shared_user_ids: [],
    shared_user_names: [],
    put_at: "2026-09-20T01:00:00Z",
    expires_on: null,
    ...overrides,
  };
}

test("same normalized name groups records without merging identity or expiry", () => {
  const rows = [
    item({ item_id: "one", label: " 麥香 ", expires_on: "2026-10-02" }),
    item({ item_id: "two", label: "麥香", owner_id: "owner-b", owner_display_name: "B", expires_on: "2026-09-23" }),
    item({ item_id: "three", label: "MILK", expires_on: null }),
    item({ item_id: "four", label: "milk", expires_on: "2026-09-25" }),
  ];
  const groups = groupInventoryItems(rows);
  assert.equal(groups.length, 2);
  const tea = groups.find(group => group.key === "麥香");
  assert.deepEqual(tea?.items.map(row => row.item_id), ["one", "two"]);
  assert.equal(tea?.earliestExpiry, "2026-09-23");
  const milk = groups.find(group => group.key === "milk");
  assert.deepEqual(milk?.items.map(row => row.item_id), ["three", "four"]);
  assert.equal(milk?.earliestExpiry, "2026-09-25");
});

test("take-out identity text includes expiry and owner without exposing UUID", () => {
  const choice = item({ item_id: "opaque-secret", expires_on: null, owner_display_name: "Enoch" });
  assert.equal(takeChoiceTitle(choice), "麥香 · 未填期限");
  assert.equal(takeChoiceOwner(choice), "Enoch");
  assert.equal(takeChoiceTitle(choice).includes(choice.item_id), false);
});

test("management sharing labels distinguish public, direct, and private access", () => {
  assert.equal(inventorySharingLabel(item({ shared: true })), "全體共用");
  assert.equal(inventorySharingLabel(item({ shared: false, shared_user_ids: ["owner-b"], shared_user_names: ["B"] })), "共用給 B");
  assert.equal(inventorySharingLabel(item({ shared: false, access_type: "SHARED_DIRECT" })), "指定共用");
  assert.equal(inventorySharingLabel(item({ shared: false, access_type: "PRIVATE_VISIBLE" })), "私人");
  assert.equal(canEditInventoryItem(item({ can_edit: true })), true);
  assert.equal(canEditInventoryItem(item({ can_edit: false })), false);
});

test("shared dashboard count includes both owner and recipient views", () => {
  assert.equal(isSharedInventoryItem(item({ shared: true })), true);
  assert.equal(isSharedInventoryItem(item({ shared_user_ids: ["user-b"], shared_user_names: ["B"] })), true);
  assert.equal(isSharedInventoryItem(item({ access_type: "SHARED_DIRECT", can_edit: false })), true);
  assert.equal(isSharedInventoryItem(item({ access_type: "PRIVATE_VISIBLE", can_edit: false })), false);
});

test("successful edit refreshes inventory and failed edit does not", async () => {
  const calls: string[] = [];
  await saveInventoryEdit(
    async () => { calls.push("update"); },
    async () => { calls.push("refresh"); },
  );
  assert.deepEqual(calls, ["update", "refresh"]);

  await assert.rejects(
    saveInventoryEdit(
      async () => { throw new Error("save failed"); },
      async () => { calls.push("unexpected refresh"); },
    ),
    /save failed/,
  );
  assert.equal(calls.includes("unexpected refresh"), false);
});

test("expired login becomes a re-identification notice instead of an operation error", () => {
  assert.deepEqual(
    describeStationPageFailure(new StationApiError("UNAUTHORIZED", "Identify your face again")),
    {
      requiresLogin: true,
      message: "登入已逾時或後端已重新啟動，請重新進行人臉辨識。",
    },
  );
});

test("ordinary page loading errors stay on the page", () => {
  assert.deepEqual(describeStationPageFailure(new Error("history unavailable")), {
    requiresLogin: false,
    message: "history unavailable",
  });
});
