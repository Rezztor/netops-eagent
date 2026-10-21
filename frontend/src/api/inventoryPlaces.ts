/**
 * The place master list: site > location > building > floor > room > rack.
 *
 * A "place" is a node at any level; "location" is only the second level. Handlers are in
 * plugins/_ea_inventory/api (inventory_places, inventory_place_save, inventory_place_merge).
 * A refusal is a 200 body `{ok: false, error, existing?}`, returned as is so the picker can offer "Use it".
 * The helpers below are pure: an ID is its codes joined by "-", so the level is the number of parts.
 */

import { apiRequest } from "./client";

export const PLACE_KINDS = ["site", "location", "building", "floor", "room", "rack"] as const;
export type PlaceKind = (typeof PLACE_KINDS)[number];

export const ROOM_TYPES = ["server_room", "comms_room", "riser", "office", "store", "other"] as const;

export interface Place {
  id: string;
  parent_id: string | null;
  kind: PlaceKind;
  code: string;
  name: string;
  active: boolean;
  room_type: string;
  address: string;
  contact: string;
  notes: string;
  /** Devices exactly here (not decommissioned) and here and below. */
  device_count: number;
  device_total: number;
}

export interface PlacesResponse {
  ok: boolean;
  places: Place[];
  room_types: string[];
  error?: string;
}

export interface PlaceInput {
  parent_id: string | null;
  name: string;
  code: string;
  room_type?: string;
  address?: string;
  contact?: string;
  notes?: string;
  /** Created one under the next, below the place itself (a small site's "Main" location and building). */
  chain?: { name: string; code: string }[];
}

export type PlaceChanges = Partial<Pick<Place, "name" | "room_type" | "address" | "contact" | "notes" | "active">>;

export interface PlaceResult {
  ok: boolean;
  place?: Place;
  created?: Place[];
  error?: string;
  /** Set when the code or the name is taken: the place that has it. */
  existing?: Place;
}

export async function listPlaces(includeInactive = true) {
  return apiRequest<PlacesResponse>("plugins/_ea_inventory/inventory_places", {
    body: { include_inactive: includeInactive },
  });
}

export async function suggestCode(parentId: string | null, name: string) {
  return apiRequest<{ ok: boolean; code?: string; error?: string }>("plugins/_ea_inventory/inventory_place_save", {
    body: { action: "suggest", parent_id: parentId, name },
  });
}

export async function createPlace(input: PlaceInput) {
  return apiRequest<PlaceResult>("plugins/_ea_inventory/inventory_place_save", {
    body: { action: "create", ...input },
  });
}

export async function updatePlace(id: string, changes: PlaceChanges) {
  return apiRequest<PlaceResult>("plugins/_ea_inventory/inventory_place_save", {
    body: { action: "update", id, ...changes },
  });
}

export async function deletePlace(id: string) {
  return apiRequest<{ ok: boolean; error?: string }>("plugins/_ea_inventory/inventory_place_save", {
    body: { action: "delete", id },
  });
}

/* --------------------------------- merge --------------------------------- */

export interface MergeResult {
  ok: boolean;
  error?: string;
  from_id: string;
  into_id: string;
  dry_run: boolean;
  /** Places that would be (or were) created under the target, in tree order. */
  create: { id: string; name: string; kind: PlaceKind }[];
  /** A place under the old one whose name is already used under the target by another code. Blocks the merge. */
  clashes: { id: string; name: string; existing: string }[];
  /** Devices that move (as one batch of proposals in Review), and pending moves redirected to the target. */
  devices: number;
  redirected: number;
  sample: { device_id: string; name: string; from: string; to: string }[];
  batch_id?: string | null;
}

export async function mergePlaces(fromId: string, intoId: string, dryRun: boolean) {
  return apiRequest<MergeResult>("plugins/_ea_inventory/inventory_place_merge", {
    body: { from_id: fromId, into_id: intoId, dry_run: dryRun },
  });
}

/* ------------------------------ pure helpers ----------------------------- */

/** The level of an ID: the number of codes in it. */
export function kindOf(id: string): PlaceKind {
  return PLACE_KINDS[id.split("-").length - 1] ?? "rack";
}

/** `DIFC-GA-GV4` becomes `["DIFC", "DIFC-GA", "DIFC-GA-GV4"]`. */
export function ancestorsOf(id: string): string[] {
  const parts = id.split("-");
  return parts.map((_, i) => parts.slice(0, i + 1).join("-"));
}

// Plain code-unit comparison is the database's "C" collation: a parent sorts directly before its children.
const treeOrder = (a: Place, b: Place) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** Every child of `parentId` (`null` for the sites), active or not, in tree order. */
export function childrenOf(places: Place[], parentId: string | null): Place[] {
  return places.filter((p) => p.parent_id === parentId).sort(treeOrder);
}

/** Active places whose every word is found, ignoring case, in the ID or in the name of the place or of any place above. */
export function searchPlaces(places: Place[], text: string): Place[] {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const byId = new Map(places.map((p) => [p.id, p]));
  return places
    .filter((p) => {
      const chain = ancestorsOf(p.id).map((id) => byId.get(id));
      if (chain.some((a) => !a?.active)) return false;
      const hay = [p.id, ...chain.map((a) => a!.name)].join("\n").toLowerCase();
      return words.every((w) => hay.includes(w));
    })
    .sort(treeOrder);
}

/** Follow a single active child downwards from `id`. Stops at a room: a rack is never picked for you. */
export function autoFill(places: Place[], id: string): string {
  let current = id;
  while (kindOf(current) !== "room" && kindOf(current) !== "rack") {
    const kids = childrenOf(places, current).filter((p) => p.active);
    if (kids.length !== 1) break;
    current = kids[0]!.id;
  }
  return current;
}
