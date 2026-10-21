import { describe, expect, it } from "vitest";

import { isSafeHref, parseMarkdown, type Block, type Inline } from "../markdown";

/** Flatten to text, so a test can assert content without walking the tree. */
function text(nodes: Inline[]): string {
  return nodes
    .map((n) =>
      n.kind === "text" || n.kind === "code" ? n.text : text(n.children),
    )
    .join("");
}

function kinds(blocks: Block[]): string[] {
  return blocks.map((b) => b.kind);
}

describe("blocks", () => {
  it("reads headings at every level", () => {
    const blocks = parseMarkdown("# One\n\n### Three");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ kind: "heading", level: 1 });
    expect(blocks[1]).toMatchObject({ kind: "heading", level: 3 });
  });

  it("joins wrapped lines into one paragraph", () => {
    const blocks = parseMarkdown("a line\nand its continuation\n\nsecond");
    expect(kinds(blocks)).toEqual(["paragraph", "paragraph"]);
    expect(text((blocks[0] as { children: Inline[] }).children)).toBe(
      "a line and its continuation",
    );
  });

  it("keeps fenced code literal", () => {
    const blocks = parseMarkdown("```python\n**not bold**\n# not a heading\n```");
    expect(blocks[0]).toEqual({
      kind: "code",
      language: "python",
      text: "**not bold**\n# not a heading",
    });
  });

  it("reads both list styles", () => {
    expect(parseMarkdown("- a\n- b")).toMatchObject([{ kind: "list", ordered: false }]);
    expect(parseMarkdown("1. a\n2. b")).toMatchObject([{ kind: "list", ordered: true }]);
    const list = parseMarkdown("- a\n- b")[0] as { items: Inline[][] };
    expect(list.items).toHaveLength(2);
  });

  it("keeps ordered numbering across a blank line", () => {
    // Two separate <ol>s both start at 1 in the browser; the source said 2.
    const blocks = parseMarkdown("1. one\n\n2. two");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ kind: "list", ordered: true });
    expect((blocks[0] as { items: Inline[][] }).items).toHaveLength(2);
  });

  it("reads a table with its header and rows", () => {
    const blocks = parseMarkdown("| Check | Result |\n|---|---|\n| Ping | ok |\n| Port | closed |");
    const table = blocks[0] as { kind: string; header: Inline[][]; rows: Inline[][][] };
    expect(table.kind).toBe("table");
    expect(table.header.map(text)).toEqual(["Check", "Result"]);
    expect(table.rows).toHaveLength(2);
    expect(table.rows[1]?.map(text)).toEqual(["Port", "closed"]);
  });

  it("treats pipes without a divider as ordinary text", () => {
    // Otherwise a shell pipeline in prose silently becomes a table.
    expect(kinds(parseMarkdown("run a | b | c and see"))).toEqual(["paragraph"]);
  });
});

describe("inline", () => {
  it("reads bold, italic and code", () => {
    const p = parseMarkdown("**b** and *i* and `c`")[0] as { children: Inline[] };
    expect(p.children.map((n) => n.kind)).toEqual([
      "strong", "text", "em", "text", "code",
    ]);
  });

  it("does not reparse inside code", () => {
    const p = parseMarkdown("`**stays literal**`")[0] as { children: Inline[] };
    expect(p.children).toEqual([{ kind: "code", text: "**stays literal**" }]);
  });

  it("leaves an asterisk inside a word alone", () => {
    // a*b*c is multiplication far more often than emphasis.
    const p = parseMarkdown("2*3*4")[0] as { children: Inline[] };
    expect(text(p.children)).toBe("2*3*4");
  });

  it("keeps glob asterisks instead of turning them into italics", () => {
    const p = parseMarkdown("Delete *.tmp and *.bak")[0] as { children: Inline[] };
    expect(p.children.every((n) => n.kind !== "em")).toBe(true);
    expect(text(p.children)).toBe("Delete *.tmp and *.bak");
  });

  it("keeps a link whose address contains a closing parenthesis", () => {
    const p = parseMarkdown(
      "see [page](https://example.test/Foo_(bar))",
    )[0] as { children: Inline[] };
    expect(p.children.find((n) => n.kind === "link")).toMatchObject({
      kind: "link",
      href: "https://example.test/Foo_(bar)",
    });
  });

  it("finds a link after brackets that are not one", () => {
    const p = parseMarkdown("see [note](not a link) and [docs](https://example.test/a)")[0] as { children: Inline[] };
    expect(p.children.find((n) => n.kind === "link")).toMatchObject({ href: "https://example.test/a" });
    expect(text(p.children)).toContain("[note](not a link)");
  });

  it("nests emphasis", () => {
    const p = parseMarkdown("**bold with `code`**")[0] as { children: Inline[] };
    expect(p.children[0]?.kind).toBe("strong");
    expect(text(p.children)).toBe("bold with code");
  });
});

describe("links are the security boundary", () => {
  it("accepts the schemes a person can safely click", () => {
    for (const href of ["https://x.test", "http://x.test", "mailto:a@b.test", "/app/skills"]) {
      expect(isSafeHref(href)).toBe(true);
    }
  });

  it("refuses everything else", () => {
    // The model chooses this string. A blocklist loses; only an allowlist holds.
    for (const href of [
      "javascript:alert(1)",
      "JavaScript:alert(1)",
      "  javascript:alert(1)",
      "data:text/html,<script>",
      "vbscript:msgbox",
      "file:///etc/passwd",
      "//evil.test/x",
      "/\\evil.test/x",
    ]) {
      expect(isSafeHref(href)).toBe(false);
    }
  });

  it("keeps a refused link as plain text rather than dropping it", () => {
    const p = parseMarkdown("[click](javascript:alert(1))")[0] as { children: Inline[] };
    expect(p.children.every((n) => n.kind !== "link")).toBe(true);
    expect(text(p.children)).toContain("click");
  });

  it("builds a link for a safe href", () => {
    const p = parseMarkdown("see [docs](https://x.test/a)")[0] as { children: Inline[] };
    expect(p.children.find((n) => n.kind === "link")).toMatchObject({
      kind: "link",
      href: "https://x.test/a",
    });
  });
});

describe("robustness", () => {
  it("survives empty and malformed input", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(() => parseMarkdown("| unclosed\n|---")).not.toThrow();
    expect(() => parseMarkdown("```\nunterminated")).not.toThrow();
    expect(() => parseMarkdown("**unclosed")).not.toThrow();
  });

  it("reads a long line of inline marks without running out of stack", () => {
    // One call per mark: ten thousand backticked words was `RangeError: Maximum call stack size exceeded`
    // while drawing, which takes the whole page with it, again at every reload of that conversation.
    const p = parseMarkdown(Array(10000).fill("`a`").join(" "))[0] as { children: Inline[] };
    expect(p.children.filter((n) => n.kind === "code")).toHaveLength(10000);
  });

  it("renders an unterminated fence as code rather than losing it", () => {
    expect(parseMarkdown("```\nbody")[0]).toMatchObject({ kind: "code", text: "body" });
  });
});
