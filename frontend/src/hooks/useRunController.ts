import { useCallback, useReducer } from "react";
import { AgentRun, ConnectionState, ToolEvent } from "@/lib/api";
import { emptyRunState, runReducer } from "@/lib/run-state";

export function useRunController() {
  const [state, dispatch] = useReducer(runReducer, emptyRunState);

  const hydrate = useCallback((run: AgentRun, events: ToolEvent[]) => {
    dispatch({ type: "hydrate", run, events });
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
    receive,
    setConnection,
    setRun,
    setError,
    clear,
  };
}
