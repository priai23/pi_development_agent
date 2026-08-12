import { describe, expect, it } from "vitest";

import { pendingFrom, visibleContent } from "./api";

describe("pending action stream parsing", () => {
  it("extracts the opaque action id and sanitized preview", () => {
    const content = 'Review this change.\n_ACTION_PENDING_||{"id":"action-1","tool":"write_file","risk_class":"C","preview":{"path":"models/a.py"}}';
    expect(visibleContent(content)).toBe("Review this change.");
    expect(pendingFrom(content)).toEqual({
      id: "action-1",
      tool: "write_file",
      risk_class: "C",
      preview: { path: "models/a.py" },
    });
  });

  it("does not expose a partial streamed action", () => {
    expect(pendingFrom('_ACTION_PENDING_||{"id"')).toBeNull();
  });
});
