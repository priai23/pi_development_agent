import { afterEach, describe, expect, it, vi } from "vitest";
import { followRun, ToolEvent } from "./api";

const completedRun = { id: "run-1", project_id: 1, status: "succeeded", prompt: "test", support_id: "s", error_message: null, retryable: false, cost_usd: 0, created_at: "2026-01-01T00:00:00Z" };
const event = (sequence: number): ToolEvent => ({ id: sequence, run_id: "run-1", sequence, event_type: "message.delta", payload: { text: String(sequence) }, created_at: "2026-01-01T00:00:00Z" });
const stream = (text: string) => new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(text)); controller.close(); } }), { status: 200 });

afterEach(() => vi.restoreAllMocks());

describe("followRun", () => {
  it("fills sequence gaps and emits every event once", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(stream('data: {"sequence":1,"type":"message.delta","payload":{"text":"1"}}\n\ndata: {"sequence":3,"type":"run.completed","payload":{"status":"succeeded"}}\n\n'))
      .mockResolvedValueOnce(new Response(JSON.stringify([event(2), { ...event(3), event_type: "run.completed" }]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(completedRun), { status: 200 }));
    const sequences: number[] = [];
    await followRun("run-1", (item) => sequences.push(item.sequence));
    expect(sequences).toEqual([1, 2, 3]);
    expect(fetchMock.mock.calls[1][0]).toContain("events?after=1");
  });

  it("reconnects after a network failure", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(stream('data: {"sequence":1,"type":"run.completed","payload":{"status":"succeeded"}}\n\n'))
      .mockResolvedValueOnce(new Response(JSON.stringify(completedRun), { status: 200 }));
    const states: string[] = [];
    const result = await followRun("run-1", () => undefined, { onConnectionState: (state) => states.push(state) });
    expect(result.status).toBe("succeeded");
    expect(states).toContain("retrying");
  });

  it("handles SSE keep-alive comments without advancing sequence or erroring", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(stream(': keep-alive\n\ndata: {"sequence":1,"type":"run.completed","payload":{"status":"succeeded"}}\n\n'))
      .mockResolvedValueOnce(new Response(JSON.stringify(completedRun), { status: 200 }));
    const sequences: number[] = [];
    const result = await followRun("run-1", (e) => sequences.push(e.sequence));
    expect(result.status).toBe("succeeded");
    expect(sequences).toEqual([1]);
  });

  it("reconnects on stream read timeout", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    // Create a stream that never pushes another chunk
    const hangingStream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"sequence":1,"type":"message.delta","payload":{"text":"hi"}}\n\n'));
      }
    });

    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(hangingStream, { status: 200 }))
      .mockResolvedValueOnce(stream('data: {"sequence":2,"type":"run.completed","payload":{"status":"succeeded"}}\n\n'))
      .mockResolvedValueOnce(new Response(JSON.stringify(completedRun), { status: 200 }));

    const states: string[] = [];
    const events: ToolEvent[] = [];
    const result = await followRun("run-1", (e) => events.push(e), {
      onConnectionState: (s) => states.push(s),
      readTimeoutMs: 50,
    });
    expect(result.status).toBe("succeeded");
    expect(states).toContain("retrying");
    expect(events.map((e) => e.sequence)).toEqual([1, 2]);
  });

  it("does not advance past an unfilled sequence gap", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(stream('data: {"sequence":2,"type":"run.completed","payload":{"status":"succeeded"}}\n\n'))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }));
    await expect(followRun("run-1", () => undefined, { maxRetries: 0 })).rejects.toThrow("Connection lost");
  });
});
