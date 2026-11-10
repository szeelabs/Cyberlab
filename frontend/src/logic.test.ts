import { describe, expect, it } from "vitest";

import { describeError, formatBytes } from "./api";
import { parseRoute } from "./ui";

describe("describeError", () => {
  it("reads plain and nested API error shapes", () => {
    expect(describeError({ detail: "workspace not found" }, "x")).toBe("workspace not found");
    expect(describeError({ detail: { status: "not_connected", detail: "daemon down" } }, "x")).toBe("daemon down");
    expect(
      describeError({ detail: { detail: "this would exceed your resource limits", problems: ["cpu: 2 in use + 2 requested > limit 3"] } }, "x"),
    ).toBe("this would exceed your resource limits: cpu: 2 in use + 2 requested > limit 3");
    expect(describeError({ detail: { detail: "needs approval", required: ["security.internet-egress"] } }, "x")).toBe(
      "needs approval: security.internet-egress",
    );
    expect(describeError("plain text", "x")).toBe("plain text");
    expect(describeError(null, "fallback")).toBe("fallback");
  });
});

describe("formatBytes", () => {
  it("uses binary units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KiB");
    expect(formatBytes(16 * 1024 * 1024)).toBe("16 MiB");
  });
});

describe("parseRoute", () => {
  it("maps hashes to pages", () => {
    expect(parseRoute("")).toEqual({ page: "dashboard" });
    expect(parseRoute("#/tools")).toEqual({ page: "tools" });
    expect(parseRoute("#/workspace/abc")).toEqual({ page: "workspace", id: "abc", tab: "setup" });
    expect(parseRoute("#/workspace/abc/files")).toEqual({ page: "workspace", id: "abc", tab: "files" });
    expect(parseRoute("#/nonsense")).toEqual({ page: "dashboard" });
  });
});
