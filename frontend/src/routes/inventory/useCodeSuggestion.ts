/** The code for a new place: suggested as the name is typed, editable, checked against what already exists.
 *  The rules (TIA-606 style short upper-case codes, unique among siblings) live on the server in
 *  `places.suggest_code`; this only asks it, and stops asking once the person has typed a code. */

import { useEffect, useRef, useState } from "react";

import { suggestCode, type Place } from "@/api/inventoryPlaces";

const SETTLE_MS = 250;
export const cleanCode = (value: string) => value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);

export function useCodeSuggestion(parentId: string | null, name: string, places: Place[], enabled = true) {
  const [code, setCode] = useState("");
  const typed = useRef(false); // the code was typed by hand: stop suggesting

  const empty = code === ""; // emptying the field hands it back to the suggestion
  useEffect(() => {
    if (!enabled || typed.current || !name.trim()) return;
    let live = true;
    const timer = setTimeout(() => {
      suggestCode(parentId, name.trim())
        .then((r) => {
          if (live && !typed.current && r.ok && r.code) setCode(r.code);
        })
        .catch(() => undefined);
    }, SETTLE_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [enabled, parentId, name, empty]);

  const id = code ? `${parentId ? `${parentId}-` : ""}${code}` : "";
  const key = name.trim().toLowerCase();
  // A code already used under the parent, or a name already used there (the server refuses both).
  const taken = !enabled
    ? undefined
    : (id ? places.find((p) => p.id === id) : undefined) ?? (key ? places.find((p) => p.parent_id === parentId && p.name.trim().toLowerCase() === key) : undefined);

  return {
    code,
    id,
    taken,
    setCode: (value: string) => {
      const clean = cleanCode(value);
      typed.current = clean !== ""; // emptying the field hands it back to the suggestion
      setCode(clean);
    },
  };
}
