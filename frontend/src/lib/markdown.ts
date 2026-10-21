/**
 * A small Markdown parser for agent output.
 *
 * Parses to data, never to HTML. The renderer builds React elements from these
 * nodes, so there is no `dangerouslySetInnerHTML` anywhere and no way for model
 * output to inject markup -- which is the whole reason this exists rather than a
 * dependency. It is deliberately partial: it covers what answers actually
 * contain (headings, emphasis, code, lists, tables, links) and renders anything
 * it does not understand as literal text, which is the safe direction.
 *
 * Parsing is separated from rendering so the grammar can be tested without a
 * DOM; the frontend has no DOM test environment.
 */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "strong"; children: Inline[] }
  | { kind: "em"; children: Inline[] }
  | { kind: "link"; href: string; children: Inline[] };

export type Block =
  | { kind: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; children: Inline[] }
  | { kind: "paragraph"; children: Inline[] }
  | { kind: "code"; language: string; text: string }
  | { kind: "list"; ordered: boolean; items: Inline[][] }
  | { kind: "table"; header: Inline[][]; rows: Inline[][][] }
  | { kind: "quote"; children: Inline[] }
  | { kind: "rule" };

/**
 * Only http, https and mailto become links.
 *
 * `javascript:` is the reason. Rendering a link whose href came from a model
 * means the model chooses what a click executes, and a scheme allowlist is the
 * only reliable defence -- blocklists lose to `java\tscript:` and friends.
 * Anything else keeps its text and loses its link.
 */
const SAFE_SCHEME = /^(https?:|mailto:)/i;

export function isSafeHref(href: string): boolean {
  const trimmed = href.trim();
  if (SAFE_SCHEME.test(trimmed)) return true;
  // Relative links stay inside the application, so they are safe; a protocol
  // relative "//host" is not, because it inherits any scheme -- and a browser
  // reads "/\host" as "//host", so a backslash is refused as well.
  return trimmed.startsWith("/") && !trimmed.startsWith("//") && !trimmed.includes("\\");
}

/**
 * Find a markdown link, allowing balanced parentheses in the destination
 * (`https://example.test/Foo_(bar)` must not cut at the first `)`).
 */
function matchLink(
  text: string,
): { index: number; length: number; label: string; href: string } | null {
  // Every `[label](` in turn: one that is not a link must not hide a link after it.
  const opener = /\[([^\]]*)\]\(/g;
  for (let open = opener.exec(text); open; open = opener.exec(text)) {
    const hrefStart = open.index + open[0].length;
    let depth = 1;
    let i = hrefStart;
    for (; i < text.length; i += 1) {
      const ch = text[i] ?? "";
      if (ch === "(") depth += 1;
      else if (ch === ")") {
        depth -= 1;
        if (depth === 0) break;
      } else if (/\s/.test(ch)) {
        // Whitespace ends a destination the same way CommonMark does.
        break;
      }
    }
    if (depth === 0 && i > hrefStart) {
      return { index: open.index, length: i + 1 - open.index, label: open[1] ?? "", href: text.slice(hrefStart, i) };
    }
  }
  return null;
}

/** Split at each inline mark in turn. What is inside a mark is split by a call of its own, so nesting works;
 *  what follows it is the next turn of the loop, so the length of a line is not a depth of calls. */
function splitInline(text: string): Inline[] {
  const out: Inline[] = [];
  let rest = text;

  while (rest) {
    // Order matters: code first, because its content is literal and must not be
    // reparsed -- `**not bold**` inside backticks stays as written.
    const code = /`([^`]+)`/.exec(rest);
    const strong = /\*\*([^*]+)\*\*/.exec(rest);
    // `(?!\.)` keeps shell globs like `*.tmp` from becoming italics.
    const em = /(?<![*\w])\*(?!\.)([^*]+)\*(?!\*)/.exec(rest);
    const link = matchLink(rest);

    const candidates = [
      code && { at: code.index, len: code[0].length, node: { kind: "code" as const, text: code[1] ?? "" } },
      strong && {
        at: strong.index,
        len: strong[0].length,
        node: { kind: "strong" as const, children: splitInline(strong[1] ?? "") },
      },
      em && {
        at: em.index,
        len: em[0].length,
        node: { kind: "em" as const, children: splitInline(em[1] ?? "") },
      },
      link && {
        at: link.index,
        len: link.length,
        node: isSafeHref(link.href)
          ? { kind: "link" as const, href: link.href.trim(), children: splitInline(link.label) }
          : { kind: "text" as const, text: rest.slice(link.index, link.index + link.length) },
      },
    ].filter(Boolean) as Array<{ at: number; len: number; node: Inline }>;

    if (candidates.length === 0) {
      out.push({ kind: "text", text: rest });
      break;
    }

    const first = candidates.reduce((a, b) => (b.at < a.at ? b : a));
    if (first.at > 0) out.push({ kind: "text", text: rest.slice(0, first.at) });
    out.push(first.node);
    rest = rest.slice(first.at + first.len);
  }

  return out;
}

function tableCells(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

const DIVIDER = /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/;

export function parseMarkdown(input: string): Block[] {
  const lines = String(input ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", children: splitInline(paragraph.join(" ").trim()) });
      paragraph = [];
    }
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";

    const fence = /^\s*```(\w*)\s*$/.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i] ?? "")) {
        body.push(lines[i] ?? "");
        i += 1;
      }
      blocks.push({ kind: "code", language: fence[1] ?? "", text: body.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "#").length as 1 | 2 | 3 | 4 | 5 | 6,
        children: splitInline((heading[2] ?? "").trim()),
      });
      continue;
    }

    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      flush();
      blocks.push({ kind: "rule" });
      continue;
    }

    // A table needs its divider on the next line, or a row of pipes is just text.
    if (line.includes("|") && DIVIDER.test(lines[i + 1] ?? "")) {
      flush();
      const header = tableCells(line).map(splitInline);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? "").includes("|") && (lines[i] ?? "").trim()) {
        rows.push(tableCells(lines[i] ?? "").map(splitInline));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flush();
      const ordered = Boolean(numbered);
      const items: Inline[][] = [];
      while (i < lines.length) {
        const current = lines[i] ?? "";
        // A blank line inside a list is still the same list when the next
        // non-empty line continues it — otherwise `2.` becomes a new <ol>
        // that restarts at 1 in the browser.
        if (!current.trim()) {
          let look = i + 1;
          while (look < lines.length && !(lines[look] ?? "").trim()) look += 1;
          const next = lines[look] ?? "";
          const continues = ordered
            ? /^\s*\d+[.)]\s+/.test(next)
            : /^\s*[-*+]\s+/.test(next);
          if (continues) {
            i = look;
            continue;
          }
          break;
        }
        const match = ordered
          ? /^\s*\d+[.)]\s+(.*)$/.exec(current)
          : /^\s*[-*+]\s+(.*)$/.exec(current);
        if (!match) break;
        items.push(splitInline((match[1] ?? "").trim()));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote) {
      flush();
      blocks.push({ kind: "quote", children: splitInline((quote[1] ?? "").trim()) });
      continue;
    }

    paragraph.push(line.trim());
  }

  flush();
  return blocks;
}
