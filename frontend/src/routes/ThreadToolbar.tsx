/**
 * Who answers, and with which model.
 *
 * Both controls existed before this: the agent profile could only be read from
 * Operations and set instance-wide, and the per-conversation model override sat
 * in a card *below* the transcript. Neither was visible while reading the
 * conversation they govern, which is the one moment the answer on screen
 * depends on them. So they sit above the log instead.
 *
 * Nothing here is new capability. `agents` and `agent_profile_set` are existing
 * endpoints, and the model override is the same `_model_config` plugin call the
 * panel underneath used to make -- it moved here rather than being duplicated,
 * because two controls for one setting is how they drift apart.
 *
 * Both loads live in the parent so the bar can decline to render at all. An
 * instance with no selectable profiles and central override turned off has
 * nothing to put here, and an empty bordered strip above the transcript reads
 * as something that failed to load.
 */

import { useState } from "react";

import { clearModelOverride, getModelOverride, setModelOverridePreset } from "@/api/authoring";
import { getModelConfig } from "@/api/models";
import { listAgentProfiles, setAgentProfile, type AgentProfile } from "@/api/operations";
import { Select } from "@/components";
import { useAsync } from "@/lib/useAsync";
import styles from "./chat.module.css";

/**
 * How full the model's context window is.
 *
 * Worth showing because this platform spends context on its own: the skill
 * catalogue is rebuilt into every prompt, and Auto Skills adds to it without
 * anyone choosing to. A number that only moves when someone types is a
 * preference; a number that moves by itself is information.
 *
 * Hidden entirely before the first turn -- there is no prompt yet, and "0%"
 * reads as a measurement rather than an absence.
 */
function ContextMeter({ tokens, limit }: { tokens: number; limit: number }) {
  if (!tokens || !limit) return null;

  const share = Math.min(1, tokens / limit);
  const percent = Math.round(share * 100);
  // Thresholds, not a gradient: the question is "should I compact this
  // conversation", which has an answer well before the window is full.
  const tone = share >= 0.9 ? styles.meterCrit : share >= 0.7 ? styles.meterWarn : "";

  return (
    <div
      className={styles.meter}
      title={`${tokens.toLocaleString()} of about ${limit.toLocaleString()} tokens in the last prompt`}
    >
      <span className={styles.meterLabel}>context</span>
      <span className={styles.meterTrack} aria-hidden="true">
        <span className={`${styles.meterFill} ${tone}`} style={{ width: `${percent}%` }} />
      </span>
      <span className={styles.meterValue}>{percent}%</span>
    </div>
  );
}

export function ThreadToolbar({
  contextId,
  activeProfile,
  running,
  onError,
  onChanged,
  focused,
  onToggleFocus,
  focusHref,
  contextTokens,
}: {
  contextId: string;
  /** The profile this conversation currently runs under, from the snapshot. */
  activeProfile: string;
  /** The agent is mid-run; the backend refuses a profile change with 409. */
  running: boolean;
  onError: (message: string) => void;
  /** Whether this thread currently has the whole window. */
  focused: boolean;
  onToggleFocus: () => void;
  /** The same screen focused, for opening in a new tab. */
  focusHref: string;
  /** Tokens in the prompt last sent, from the snapshot. */
  contextTokens: number;
  onChanged: () => void;
}) {
  // Instance-wide, so it does not reload with the conversation.
  const profiles = useAsync<AgentProfile[]>(() => listAgentProfiles(), []);
  const override = useAsync(() => getModelOverride(contextId), [contextId]);
  const config = useAsync(() => getModelConfig(), []);
  const [busy, setBusy] = useState(false);

  // A failure to list profiles is not worth a banner over the transcript: the
  // conversation still works, it just cannot be re-pointed from here.
  const profileList = profiles.state.status === "ready" ? profiles.state.data : [];
  const showProfiles = profileList.length > 0;

  const modelOverride = override.state.status === "ready" ? override.state.data : null;
  const presets = config.state.status === "ready" ? config.state.data.presets : null;

  // The window the meter measures against. A per-chat override changes which
  // model answers, so its length wins where one is set; the configured chat
  // model is the fallback. Zero hides the meter rather than guessing a limit --
  // a percentage of an assumed window is worse than no percentage.
  const contextLimit =
    config.state.status === "ready"
      ? Number(config.state.data.config?.chat_model?.ctx_length ?? 0)
      : 0;
  // Overriding can be turned off centrally; when it is, offering the control
  // would be offering something the backend will refuse.
  const showModel = modelOverride !== null && presets !== null && modelOverride.allowed !== false;

  if (!showProfiles && !showModel) return null;

  const activePreset = modelOverride?.override ? modelOverride.effective_preset : "";

  async function runModelChange(action: () => Promise<{ error?: string }>) {
    setBusy(true);
    try {
      const result = await action();
      if (result.error) throw new Error(result.error);
      override.reload();
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function chooseProfile(profile: string) {
    setBusy(true);
    try {
      const result = await setAgentProfile(contextId, profile);
      if (result.error) throw new Error(result.error);
      onChanged();
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.threadHead}>
      {focused && (
        // The rail is hidden in focus mode, so this is the only way back that
        // does not require knowing about Escape.
        <button type="button" className={styles.focusBack} onClick={onToggleFocus}>
          ← Back to chat
        </button>
      )}
      {showProfiles && (
        <>
          <span className={styles.threadHeadLabel}>Agent</span>
          <div className={styles.pillRow} role="group" aria-label="Agent profile">
            {profileList.map((profile) => {
              const active = profile.key === activeProfile;
              return (
                <button
                  key={profile.key}
                  type="button"
                  aria-pressed={active}
                  disabled={busy || !contextId || (running && !active)}
                  title={
                    running && !active
                      ? "The profile can be changed once the current run finishes."
                      : undefined
                  }
                  className={active ? `${styles.pill} ${styles.pillActive}` : styles.pill}
                  onClick={() => {
                    if (active) return;
                    void chooseProfile(profile.key);
                  }}
                >
                  {profile.label || profile.key}
                </button>
              );
            })}
          </div>
        </>
      )}

      <div style={{ flex: 1 }} />

      {showModel && (
        <>
          <span className={styles.threadHeadLabel}>Model</span>
          <Select
            value={activePreset || ""}
            disabled={busy}
            aria-label="Model for this conversation"
            title={
              modelOverride.override
                ? "This conversation is not using the instance default."
                : `Using the instance default (${modelOverride.configured_preset || "unset"}).`
            }
            onChange={(event) => {
              const value = event.target.value;
              void runModelChange(() =>
                value ? setModelOverridePreset(contextId, value) : clearModelOverride(contextId),
              );
            }}
          >
            <option value="">Instance default</option>
            {presets.map((preset) => (
              <option key={preset.name} value={preset.name}>
                {preset.name}
              </option>
            ))}
          </Select>
        </>
      )}
      <ContextMeter tokens={contextTokens} limit={contextLimit} />
      <div className={styles.threadHeadSpacer} />
      <button
        type="button"
        className={styles.headAction}
        onClick={onToggleFocus}
        title={focused ? "Restore the normal layout (Esc)" : "Give this conversation the whole window"}
        aria-label={focused ? "Exit focus mode" : "Expand conversation"}
      >
        {focused ? "\u2935" : "\u2922"}
      </button>
      {!focused && (
        // The same screen, focused. It is a link rather than a window.open so
        // the browser's own "open in new tab" gestures work on it too.
        <a
          className={styles.headAction}
          href={focusHref}
          target="_blank"
          rel="noopener noreferrer"
          title="Open this conversation in a new tab"
          aria-label="Open in a new tab"
        >
          &#x29C9;
        </a>
      )}
    </div>
  );
}
