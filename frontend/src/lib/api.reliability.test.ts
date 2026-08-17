import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, errorMessage, parseSSE } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("API reliability", () => {
  it("normalizes structured errors with support IDs", () => {
    expect(errorMessage({ detail: { message: "Run is active", support_id: "support-1" } })).toBe("Run is active · Support support-1");
    expect(errorMessage({ detail: [{ msg: "Invalid email" }, { msg: "Password is short" }] })).toBe("Invalid email; Password is short");
    expect(errorMessage("Gateway unavailable")).toBe("Gateway unavailable");
  });

  it("normalizes a backend connection failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(apiFetch("/auth/setup-status")).rejects.toThrow("Backend unavailable at");
  });

  it("parses fragmented CRLF events, multiline data, and heartbeats", () => {
    const first = parseSSE(': keep-alive\r\n\r\nid: 1\r\ndata: {"sequence":1,"type":"message.delta",\r\ndata: "payload":{"text":"hi"}}\r\n');
    expect(first.records).toEqual([]);
    const second = parseSSE(first.rest + "\r\n");
    expect(second.records).toHaveLength(1);
    expect(second.records[0].payload).toEqual({ text: "hi" });
  });

  it("ignores malformed and incomplete records without advancing", () => {
    expect(parseSSE("data: {bad}\n\n").records).toEqual([]);
    expect(parseSSE('data: {"sequence":2').rest).toContain("sequence");
  });
});
