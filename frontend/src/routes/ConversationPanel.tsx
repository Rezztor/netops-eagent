/**
 * Per-conversation settings: the documents this conversation has open.
 *
 * Scoped to one conversation rather than the instance, which is why it lives
 * beside the transcript rather than in Settings.
 *
 * Two cards have left this panel. The model override moved to the thread's own
 * toolbar (`ThreadToolbar.tsx`), above the transcript it governs. The goal card
 * went with the goal function itself -- see ADR 0031: this agent acts on the
 * user's approval, so a standing objective it pursues between turns is the wrong
 * shape for it.
 */

import { listOpenDocuments, type OpenDocument } from "@/api/authoring";
import { Card, Mono, Row, Tag } from "@/components";
import { useAsync } from "@/lib/useAsync";

export function ConversationPanel({ contextId }: { contextId: string }) {
  if (!contextId) return null;
  return <OpenDocuments contextId={contextId} />;
}

function OpenDocuments({ contextId }: { contextId: string }) {
  const { state } = useAsync<OpenDocument[]>(() => listOpenDocuments(contextId), [contextId]);

  if (state.status !== "ready" || state.data.length === 0) return null;

  return (
    <Card title="Open documents" description="Files this conversation has open in the editor.">
      {state.data.map((document, index) => (
        <Row key={document.path || index} label={document.title || document.path || "Untitled"}>
          <Mono>{document.path || "—"}</Mono>
          {document.dirty && <Tag tone="warn">unsaved</Tag>}
        </Row>
      ))}
    </Card>
  );
}
