import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

describe("production build", () => {
  it("does not ship source maps", () => {
    const configPath = join(dirname(fileURLToPath(import.meta.url)), "../../../vite.config.ts");
    const source = readFileSync(configPath, "utf8");
    // `sourcemap: true` published the TypeScript sources alongside the bundle.
    expect(source).toMatch(/sourcemap:\s*false/);
    expect(source).not.toMatch(/sourcemap:\s*true/);
  });
});
