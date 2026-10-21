/**
 * What the Network screens say when the executor gives them nothing.
 *
 * "The executor refused" plus Retry on every tab was what an instance with no
 * executor configured showed -- a fault, to anyone reading it, when it is a
 * step nobody has taken yet. A missing address or token is a setup state: it
 * names the settings page for an administrator and the administrator for
 * everyone else, and offers no Retry, because retrying changes nothing.
 */

import { Link } from "react-router-dom";

import { PERMISSIONS } from "@/api/types";
import { Button, CenteredMessage } from "@/components";
import { useCan } from "@/lib/session";

/** The executor client's NotConfigured messages (plugins/_ea_ansible/helpers/executor_client.py). */
export function isNotSetUp(message: string | undefined): boolean {
  return /not configured|no executor token/i.test(message ?? "");
}

export function ExecutorRefused({ error, onRetry }: { error: string | undefined; onRetry: () => void }) {
  const admin = useCan(PERMISSIONS.settingsManage);
  if (isNotSetUp(error)) {
    return (
      <CenteredMessage
        title="Network automation is not set up yet"
        detail={
          admin ? (
            <>
              Give it the executor's address and token in{" "}
              <Link to="/settings/network-automation">Settings, Network automation</Link>.
            </>
          ) : (
            "Ask your administrator to set it up."
          )
        }
      />
    );
  }
  return (
    <CenteredMessage
      title="The executor refused"
      detail={error}
      action={<Button variant="primary" onClick={onRetry}>Retry</Button>}
    />
  );
}
