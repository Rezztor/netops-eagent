import { describe, expect, it } from "vitest";

import { matchesQuery, selectRows } from "../useTableControls";

interface Person {
  name: string;
  role: string;
  age: number;
}

const people: Person[] = [
  { name: "Ada Lovelace", role: "admin", age: 36 },
  { name: "Grace Hopper", role: "power", age: 85 },
  { name: "Alan Turing", role: "user", age: 41 },
];

describe("matchesQuery", () => {
  it("matches when every token is found in some field", () => {
    expect(matchesQuery(["Ada Lovelace", "admin"], "ada admin")).toBe(true);
    expect(matchesQuery(["Ada Lovelace", "admin"], "ada power")).toBe(false);
  });

  it("is case-insensitive and ignores empty queries", () => {
    expect(matchesQuery(["Grace"], "GRACE")).toBe(true);
    expect(matchesQuery(["Grace"], "   ")).toBe(true);
  });

  it("skips null and undefined fields", () => {
    expect(matchesQuery([null, undefined, "keep"], "keep")).toBe(true);
  });
});

describe("selectRows", () => {
  const search = (p: Person) => [p.name, p.role];
  const comparators = {
    name: (a: Person, b: Person) => a.name.localeCompare(b.name),
    age: (a: Person, b: Person) => a.age - b.age,
  };

  it("filters by the free-text query across searchable fields", () => {
    const rows = selectRows(people, { query: "admin", search });
    expect(rows.map((p) => p.name)).toEqual(["Ada Lovelace"]);
  });

  it("applies an extra predicate before searching", () => {
    const rows = selectRows(people, { filter: (p) => p.age > 40 });
    expect(rows.map((p) => p.name)).toEqual(["Grace Hopper", "Alan Turing"]);
  });

  it("sorts ascending and descending without mutating the input", () => {
    const asc = selectRows(people, { comparators, sort: { key: "age", direction: "asc" } });
    expect(asc.map((p) => p.age)).toEqual([36, 41, 85]);
    const desc = selectRows(people, { comparators, sort: { key: "age", direction: "desc" } });
    expect(desc.map((p) => p.age)).toEqual([85, 41, 36]);
    // The original order is untouched.
    expect(people.map((p) => p.age)).toEqual([36, 85, 41]);
  });

  it("ignores an unknown sort key", () => {
    const rows = selectRows(people, { comparators, sort: { key: "missing", direction: "asc" } });
    expect(rows.map((p) => p.name)).toEqual(people.map((p) => p.name));
  });
});
