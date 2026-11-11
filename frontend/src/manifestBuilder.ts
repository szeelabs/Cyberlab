// Turns the beginner form into a Universe Manifest (YAML). Pure functions, unit-tested.
// Every string is written as a JSON string, which is also valid YAML, so names, hosts or
// descriptions can never break the document's structure.

export type NetworkMode = "isolated" | "lab" | "restricted-egress" | "internet";

export type EnvDraft = {
  name: string;
  baseImage: string;
  toolPacks: string[];
  size: keyof typeof SIZES;
  network: NetworkMode;
  allowedEgress: string[];
  idleTimeoutMinutes: number | null;
  ttlHours: number | null;
  ephemeral: boolean;
  desktop: boolean;
};

export type LabDraft = { name: string; description: string; environments: EnvDraft[] };

export const SIZES = {
  small: { label: "Small (0.5 CPU, 1 GiB RAM, 4 GiB disk)", cpu: 0.5, memoryMiB: 1024, storageGiB: 4 },
  medium: { label: "Medium (1 CPU, 2 GiB RAM, 8 GiB disk)", cpu: 1, memoryMiB: 2048, storageGiB: 8 },
  large: { label: "Large (2 CPU, 4 GiB RAM, 16 GiB disk)", cpu: 2, memoryMiB: 4096, storageGiB: 16 },
} as const;

export const NETWORKS: Record<NetworkMode, string> = {
  isolated: "Isolated: no network at all",
  lab: "Lab: only this workspace's own machines (recommended)",
  "restricted-egress": "Lab + only the websites you list",
  internet: "Full internet (needs your approval)",
};

export const SLUG = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

export function newEnvironment(index: number): EnvDraft {
  return {
    name: index === 0 ? "shell" : `machine-${index + 1}`,
    baseImage: "debian-12",
    toolPacks: [],
    size: "small",
    network: "lab",
    allowedEgress: [],
    idleTimeoutMinutes: 60,
    ttlHours: null,
    ephemeral: false,
    desktop: false,
  };
}

/** Problems a beginner can fix in the form before the server checks the rest. */
export function formProblems(lab: LabDraft): string[] {
  const out: string[] = [];
  if (!SLUG.test(lab.name)) out.push("Lab name: use lowercase letters, digits and dashes (e.g. web-practice).");
  if (lab.environments.length === 0) out.push("Add at least one machine.");
  const seen = new Set<string>();
  lab.environments.forEach((e, i) => {
    const label = `Machine ${i + 1}`;
    if (!SLUG.test(e.name)) out.push(`${label}: name must be lowercase letters, digits and dashes.`);
    if (seen.has(e.name)) out.push(`${label}: another machine is already called "${e.name}".`);
    seen.add(e.name);
    if (e.desktop && e.size === "small")
      out.push(`${label}: the remote desktop needs about 1 GiB more memory; choose Medium or Large.`);
    if (e.network === "restricted-egress" && e.allowedEgress.length === 0)
      out.push(`${label}: list at least one website, or choose another network option.`);
  });
  return out;
}

const q = (s: string) => JSON.stringify(s);

export function toYaml(lab: LabDraft): string {
  const lines = [
    "apiVersion: cyberlab.dev/v1alpha1",
    "kind: Universe",
    "metadata:",
    `  name: ${q(lab.name)}`,
  ];
  if (lab.description.trim()) lines.push(`  description: ${q(lab.description.trim())}`);
  lines.push("spec:", "  environments:");
  for (const e of lab.environments) {
    const size = SIZES[e.size];
    lines.push(
      `    - name: ${q(e.name)}`,
      `      baseImage: ${q(e.baseImage)}`,
      `      resources: {cpu: ${size.cpu}, memoryMiB: ${size.memoryMiB}, storageGiB: ${size.storageGiB}}`,
      `      toolPacks: [${e.toolPacks.map(q).join(", ")}]`,
    );
    if (e.network === "restricted-egress") {
      lines.push(`      network: {mode: ${q(e.network)}, allowedEgress: [${e.allowedEgress.map(q).join(", ")}]}`);
    } else {
      lines.push(`      network: {mode: ${q(e.network)}}`);
    }
    const lc = [`idleTimeoutMinutes: ${e.idleTimeoutMinutes ?? "null"}`];
    if (e.ttlHours) lc.push(`ttlHours: ${e.ttlHours}`);
    if (e.ephemeral) lc.push("ephemeral: true");
    lines.push(`      lifecycle: {${lc.join(", ")}}`);
    if (e.desktop) lines.push("      access: {desktop: true}");
  }
  return lines.join("\n") + "\n";
}

/** Split a free-text list of hostnames ("a.com, b.org\nc.net") into clean entries. */
export function parseHosts(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).map((h) => h.trim().toLowerCase()).filter(Boolean))];
}
