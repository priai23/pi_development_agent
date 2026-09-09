import { useCallback, useReducer } from "react";
import { AgentRun, ConnectionState, ToolEvent } from "@/lib/api";
import { ChatMessage, emptyRunState, runReducer } from "@/lib/run-state";

export function useRunController() {
  const [state, dispatch] = useReducer(runReducer, emptyRunState);

  const hydrate = useCallback((run: AgentRun, events: ToolEvent[], priorTranscript?: ChatMessage[]) => {
    dispatch({ type: "hydrate", run, events, priorTranscript });
  }, []);

  const continueRun = useCallback((run: AgentRun) => {
    dispatch({ type: "continue_run", run });
  }, []);

  const setTranscript = useCallback((transcript: ChatMessage[]) => {
    dispatch({ type: "set_transcript", transcript });
  }, []);

  const receive = useCallback((event: ToolEvent) => {
    dispatch({ type: "event", event });
  }, []);

  const setConnection = useCallback((connection: ConnectionState | "idle") => {
    dispatch({ type: "connection", connection });
  }, []);

  const setRun = useCallback((run: AgentRun) => {
    dispatch({ type: "run_status", run });
  }, []);

  const setError = useCallback((error: string) => {
    dispatch({ type: "error", error });
  }, []);

  const clear = useCallback(() => {
    dispatch({ type: "clear" });
  }, []);

  return {
    state,
    hydrate,
    continueRun,
    setTranscript,
    receive,
    setConnection,
    setRun,
    setError,
    clear,
  };
}

