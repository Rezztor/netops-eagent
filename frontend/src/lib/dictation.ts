/**
 * Dictation into the composer, where the browser offers it.
 *
 * This is the Web Speech API -- the browser's, not the agent's. Nothing is sent
 * to this backend and no endpoint is involved. Worth knowing, and worth saying
 * out loud on a self-hosted console: in Chrome and Edge the implementation is
 * *not* local, it streams the microphone to the browser vendor's speech service.
 * Firefox does not implement it at all. So the control is offered only when the
 * browser actually has it, and it is never the only way to enter a message.
 *
 * `supported` is resolved once at mount rather than per render: it cannot change
 * for the life of the page, and a control that appears halfway through typing is
 * worse than one that was never there.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/*
 * The Web Speech API is absent from TypeScript's DOM library, so the shapes this
 * module touches are declared here. Deliberately minimal -- only what is read --
 * rather than a speculative transcription of the whole specification.
 */

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  readonly length: number;
  readonly isFinal: boolean;
  [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: {
    readonly length: number;
    [index: number]: SpeechRecognitionResultLike;
  };
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function speechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const scope = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

export interface Dictation {
  /** False when the browser has no speech recognition; hide the control. */
  supported: boolean;
  listening: boolean;
  toggle: () => void;
}

/** Shown on the dictation control: Chrome/Edge stream the mic to the vendor. */
export const DICTATION_START_LABEL =
  "Dictate a message (the browser sends the audio to its maker's speech service)";
export const DICTATION_STOP_LABEL = "Stop dictating";

/**
 * @param onText  Called with each finalised phrase, to append to the draft.
 * @param onError Called when recognition fails, with a message worth showing.
 */
export function useDictation(
  onText: (text: string) => void,
  onError: (message: string) => void,
): Dictation {
  const [supported] = useState(() => speechRecognitionConstructor() !== null);
  const [listening, setListening] = useState(false);
  const recognition = useRef<SpeechRecognitionLike | null>(null);

  // Held in refs so the recognition object is built once and still calls the
  // current handlers: rebuilding it on every render would drop the session.
  const textHandler = useRef(onText);
  const errorHandler = useRef(onError);
  useEffect(() => {
    textHandler.current = onText;
    errorHandler.current = onError;
  }, [onText, onError]);

  // Stop listening if the screen goes away; the microphone should not outlive
  // the control that opened it.
  useEffect(() => {
    return () => {
      recognition.current?.abort();
      recognition.current = null;
    };
  }, []);

  const toggle = useCallback(() => {
    if (recognition.current) {
      recognition.current.stop();
      return;
    }

    const Recognition = speechRecognitionConstructor();
    if (!Recognition) return;

    const session = new Recognition();
    session.lang = navigator.language || "en-US";
    // Keep listening across pauses, and take only settled text: interim results
    // rewrite themselves, which in a textarea reads as the words changing their
    // minds.
    session.continuous = true;
    session.interimResults = false;

    session.onresult = (event) => {
      let text = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (result?.isFinal) text += result[0]?.transcript ?? "";
      }
      if (text.trim()) textHandler.current(text.trim());
    };

    session.onerror = (event) => {
      // Not an error worth a banner: the user stopped talking, or stopped it.
      if (event.error === "aborted" || event.error === "no-speech") return;
      errorHandler.current(
        event.error === "not-allowed"
          ? "Microphone access was refused, so dictation cannot start."
          : `Dictation failed (${event.error || "unknown error"}).`,
      );
    };

    session.onend = () => {
      recognition.current = null;
      setListening(false);
    };

    try {
      session.start();
      recognition.current = session;
      setListening(true);
    } catch (caught) {
      errorHandler.current(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  return { supported, listening, toggle };
}
