/**
 * Renders parsed Markdown as React elements.
 *
 * No `dangerouslySetInnerHTML`: every node becomes an element, so model output
 * is text in a text position and cannot introduce markup. The parser already
 * refused any link scheme a click could execute; `rel` here closes the other
 * half, where a new tab could reach back through `window.opener`.
 */

import type { ReactNode } from "react";

import { parseMarkdown, type Block, type Inline } from "./markdown";
import styles from "@/routes/chat.module.css";

function renderInline(nodes: Inline[], keyPrefix = ""): ReactNode[] {
  return nodes.map((node, index) => {
    const key = `${keyPrefix}${index}`;
    if (node.kind === "text") return <span key={key}>{node.text}</span>;
    if (node.kind === "code") return <code key={key} className={styles.mdCode}>{node.text}</code>;
    if (node.kind === "strong") return <strong key={key}>{renderInline(node.children, `${key}-`)}</strong>;
    if (node.kind === "em") return <em key={key}>{renderInline(node.children, `${key}-`)}</em>;
    return (
      <a key={key} href={node.href} target="_blank" rel="noopener noreferrer nofollow">
        {renderInline(node.children, `${key}-`)}
      </a>
    );
  });
}

function renderBlock(block: Block, key: string): ReactNode {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${Math.min(block.level + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
      // Shifted down two levels: the page already owns h1 and h2, and an answer
      // is not a document of its own.
      return <Tag key={key} className={styles.mdHeading}>{renderInline(block.children, key)}</Tag>;
    }
    case "paragraph":
      return <p key={key} className={styles.mdParagraph}>{renderInline(block.children, key)}</p>;
    case "code":
      return (
        <pre key={key} className={styles.mdPre}>
          <code>{block.text}</code>
        </pre>
      );
    case "list": {
      const items = block.items.map((item, i) => (
        <li key={`${key}-${i}`}>{renderInline(item, `${key}-${i}-`)}</li>
      ));
      return block.ordered
        ? <ol key={key} className={styles.mdList}>{items}</ol>
        : <ul key={key} className={styles.mdList}>{items}</ul>;
    }
    case "table":
      return (
        <div key={key} className="scroll-x">
          <table className={styles.mdTable}>
            <thead>
              <tr>{block.header.map((cell, i) => <th key={i}>{renderInline(cell, `${key}-h${i}-`)}</th>)}</tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>{row.map((cell, c) => <td key={c}>{renderInline(cell, `${key}-${r}-${c}-`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "quote":
      return <blockquote key={key} className={styles.mdQuote}>{renderInline(block.children, key)}</blockquote>;
    case "rule":
      return <hr key={key} className={styles.mdRule} />;
  }
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseMarkdown(text);
  return <>{blocks.map((block, index) => renderBlock(block, `b${index}`))}</>;
}
