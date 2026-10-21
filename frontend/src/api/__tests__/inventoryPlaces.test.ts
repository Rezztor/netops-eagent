import { describe, expect, it } from "vitest";

import { ancestorsOf, autoFill, childrenOf, kindOf, searchPlaces, type Place } from "../inventoryPlaces";

function place(id: string, name: string, active = true): Place {
  const parts = id.split("-");
  return {
    id,
    parent_id: parts.length > 1 ? parts.slice(0, -1).join("-") : null,
    kind: kindOf(id),
    code: parts[parts.length - 1]!,
    name,
    active,
    room_type: "",
    address: "",
    contact: "",
    notes: "",
    device_count: 0,
    device_total: 0,
  };
}

const PLACES = [
  place("DIFC", "DIFC"),
  place("DIFC-GA", "Gate Avenue"),
  place("DIFC-GA-GV4", "Gate Village Building 4"),
  place("DIFC-GA-GV4-L1", "Level 1"),
  place("DIFC-GA-GV4-L1-R01", "Room 1"),
  place("DIFC-GA-GV4-L1-R02", "Room 2"),
  place("DIFC-GA-GV4-L1-R02-RK01", "Rack 1"),
  place("DIFC-GB", "Gate Boulevard"),
  place("JBR", "Jumeirah"),
  place("JBR-M", "Main"),
  place("JBR-M-M", "Main"),
  place("JBR-M-M-GF", "Ground floor"),
  place("JBR-M-M-GF-R01", "Comms"),
  place("JBR-M-M-GF-R01-RK01", "Rack 1"),
  place("OLD", "Closed site", false),
  place("OLD-M", "Main"),
];

describe("ids", () => {
  it("reads the level and the ancestors from the ID alone", () => {
    expect(kindOf("DIFC")).toBe("site");
    expect(kindOf("DIFC-GA-GV4-L1-R02-RK01")).toBe("rack");
    expect(ancestorsOf("DIFC-GA-GV4")).toEqual(["DIFC", "DIFC-GA", "DIFC-GA-GV4"]);
  });

  it("lists children in tree order, inactive ones included", () => {
    expect(childrenOf(PLACES, "DIFC").map((p) => p.id)).toEqual(["DIFC-GA", "DIFC-GB"]);
    expect(childrenOf(PLACES, null).map((p) => p.id)).toEqual(["DIFC", "JBR", "OLD"]);
  });
});

describe("searchPlaces", () => {
  it("needs every word, in the ID or in the name of the place or a place above, in any case", () => {
    expect(searchPlaces(PLACES, "gv4 r02").map((p) => p.id)).toEqual([
      "DIFC-GA-GV4-L1-R02",
      "DIFC-GA-GV4-L1-R02-RK01",
    ]);
    expect(searchPlaces(PLACES, "GATE village RACK").map((p) => p.id)).toEqual(["DIFC-GA-GV4-L1-R02-RK01"]);
    expect(searchPlaces(PLACES, "comms").map((p) => p.id)).toEqual(["JBR-M-M-GF-R01", "JBR-M-M-GF-R01-RK01"]);
  });

  it("lists nothing for empty text, and never an inactive place or anything under one", () => {
    expect(searchPlaces(PLACES, "  ")).toEqual([]);
    expect(searchPlaces(PLACES, "closed")).toEqual([]);
    expect(searchPlaces(PLACES, "old")).toEqual([]);
  });
});

describe("autoFill", () => {
  it("follows single children down to the room and never fills a rack", () => {
    expect(autoFill(PLACES, "JBR")).toBe("JBR-M-M-GF-R01");
    expect(autoFill(PLACES, "JBR-M-M-GF-R01")).toBe("JBR-M-M-GF-R01");
  });

  it("stops where there is a choice, and ignores inactive children", () => {
    expect(autoFill(PLACES, "DIFC")).toBe("DIFC");
    expect(autoFill(PLACES, "DIFC-GA-GV4-L1")).toBe("DIFC-GA-GV4-L1");
    const two = [place("S", "S"), place("S-A", "A"), place("S-B", "B", false)];
    expect(autoFill(two, "S")).toBe("S-A");
  });
});
