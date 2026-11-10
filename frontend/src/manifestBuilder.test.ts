import { describe, expect, it } from "vitest";

import { formProblems, newEnvironment, parseHosts, toYaml, type LabDraft } from "./manifestBuilder";

const lab = (over: Partial<LabDraft> = {}): LabDraft => ({
  name: "web-practice",
  description: "",
  environments: [newEnvironment(0)],
  ...over,
});

describe("toYaml", () => {
  it("writes a complete manifest", () => {
    const y = toYaml(lab({ description: "My first lab" }));
    expect(y).toContain('name: "web-practice"');
    expect(y).toContain('description: "My first lab"');
    expect(y).toContain("resources: {cpu: 0.5, memoryMiB: 1024, storageGiB: 4}");
    expect(y).toContain('network: {mode: "lab"}');
    expect(y).toContain("lifecycle: {idleTimeoutMinutes: 60}");
  });

  it("includes allowed websites only for restricted-egress", () => {
    const e = { ...newEnvironment(0), network: "restricted-egress" as const, allowedEgress: ["ghcr.io"] };
    expect(toYaml(lab({ environments: [e] }))).toContain('allowedEgress: ["ghcr.io"]');
  });

  it("can't be broken by odd text in descriptions", () => {
    const y = toYaml(lab({ description: 'say "hi"\nnew: line # not a comment' }));
    expect(y).toContain('description: "say \\"hi\\"\\nnew: line # not a comment"');
    expect(y.split("\n").filter((l) => l.startsWith("new:"))).toEqual([]);
  });

  it("writes ttl and ephemeral only when set", () => {
    const e = { ...newEnvironment(0), ttlHours: 8, ephemeral: true, idleTimeoutMinutes: null };
    expect(toYaml(lab({ environments: [e] }))).toContain("lifecycle: {idleTimeoutMinutes: null, ttlHours: 8, ephemeral: true}");
  });
});

describe("formProblems", () => {
  it("finds what a beginner can fix", () => {
    expect(formProblems(lab())).toEqual([]);
    expect(formProblems(lab({ name: "Web Practice" }))[0]).toMatch(/Lab name/);
    expect(formProblems(lab({ environments: [] }))).toContain("Add at least one machine.");
    const twins = [newEnvironment(0), newEnvironment(0)];
    expect(formProblems(lab({ environments: twins })).join()).toMatch(/already called "shell"/);
    const noSites = { ...newEnvironment(0), network: "restricted-egress" as const };
    expect(formProblems(lab({ environments: [noSites] })).join()).toMatch(/at least one website/);
  });
});

describe("parseHosts", () => {
  it("splits, trims, lowercases and de-duplicates", () => {
    expect(parseHosts("GHCR.io, mirror.gcr.io\n ghcr.io ")).toEqual(["ghcr.io", "mirror.gcr.io"]);
    expect(parseHosts("  ")).toEqual([]);
  });
});
