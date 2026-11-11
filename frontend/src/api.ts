// Typed client for the CyberLab control-plane API.
// The API token (only needed when the server uses real sign-in) is kept in sessionStorage,
// so it disappears when the browser tab closes.

export type Finding = { severity: "error" | "approval" | "warning"; code: string; message: string; path: string };

export type Plan = {
  valid: boolean;
  findings: Finding[];
  total_cpu: number;
  total_memory_mib: number;
  total_storage_gib: number;
  requires_approval: boolean;
};

export type Workspace = {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  manifest_revision: number | null;
};

export type Environment = {
  id: string;
  name: string;
  revision: number;
  state: "provisioning" | "running" | "stopped" | "failed" | "destroyed" | "missing";
  lastError: string | null;
  toolPacks: string[];
  toolPacksInstalled: boolean;
  image: string | null;
  lastActivityAt: string | null;
  expiresAt: string | null;
  createdAt: string;
};

export type Snapshot = {
  id: string;
  environment: string;
  label: string;
  sizeBytes: number;
  createdAt: string;
};

export type FileEntry = { name: string; type: "file" | "directory" | "symlink" | "other"; size: number; modified: number };

export type Usage = {
  usage: { environments: number; cpu: number; memoryMiB: number; storageGiB: number };
  limits: { environments: number; cpu: number; memoryMiB: number; storageGiB: number };
};

export type AiStatus = { provider: string | null; status: "READY" | "OFF"; detail: string; paid: boolean; model?: string | null };

export type ArchitectDraft =
  | { provider: string; refused: true; reason: string }
  | { provider: string; refused: false; explanation: string; manifestYaml: string; plan: Plan };

export type ServiceStatus = { status: "CONNECTED" | "NOT CONNECTED"; detail: string; [k: string]: unknown };

export type ToolPack = {
  id: string;
  name: string;
  version: string;
  category: string;
  description: string;
  risk: "low" | "medium" | "high";
  status: string;
  compatibleBases: string[];
  tools: { name: string; purpose: string; homepage?: string | null; package?: string | null }[];
};

export type Category = { id: string; name: string; description: string; toolPackCount: number };

export class ApiError extends Error {
  constructor(public status: number, message: string, public body: unknown) {
    super(message);
  }
}

const TOKEN_KEY = "cyberlab.token";

export function getToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token simply isn't remembered */
  }
}

/** Turn an API error body into one readable sentence. */
export function describeError(body: unknown, fallback: string): string {
  if (typeof body === "string") return body;
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") {
      const o = d as Record<string, unknown>;
      const main = typeof o.detail === "string" ? o.detail : typeof o.status === "string" ? o.status : fallback;
      const extra = Array.isArray(o.problems) ? `: ${o.problems.join("; ")}` : Array.isArray(o.required) ? `: ${o.required.join(", ")}` : "";
      return main + extra;
    }
  }
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(path, { ...init, headers });
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  const body = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, describeError(body, `${res.status} ${res.statusText}`), body);
  return body as T;
}

const json = (method: string, data?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: data === undefined ? undefined : JSON.stringify(data),
});

const enc = encodeURIComponent;
const ws = (id: string) => `/v1/workspaces/${enc(id)}`;
const env = (wsId: string, envId: string) => `${ws(wsId)}/environments/${enc(envId)}`;

export const api = {
  me: () => request<{ subject: string; email: string | null }>("/v1/me"),
  usage: () => request<Usage>("/v1/me/usage"),
  execution: () => request<ServiceStatus>("/v1/system/execution"),
  storage: () => request<ServiceStatus>("/v1/system/storage"),
  ai: () => request<AiStatus>("/v1/system/ai"),
  draftLab: (prompt: string) =>
    request<ArchitectDraft>("/v1/architect/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    }),

  categories: () => request<Category[]>("/v1/registry/categories"),
  toolPacks: () => request<ToolPack[]>("/v1/registry/tool-packs"),
  baseImages: () => request<{ id: string; name: string }[]>("/v1/registry/base-images"),

  workspaces: () => request<Workspace[]>("/v1/workspaces"),
  createWorkspace: (name: string, description = "") => request<Workspace>("/v1/workspaces", json("POST", { name, description })),
  workspace: (id: string) => request<Workspace>(ws(id)),
  deleteWorkspace: (id: string) => request<void>(ws(id), { method: "DELETE" }),

  validate: (text: string) =>
    request<Plan>("/v1/manifests/validate", { method: "POST", headers: { "Content-Type": "application/yaml" }, body: text }),
  manifest: (id: string) => request<string>(`${ws(id)}/manifest`),
  saveManifest: (id: string, text: string) =>
    request<Plan & { revision: number }>(`${ws(id)}/manifest`, { method: "PUT", headers: { "Content-Type": "application/yaml" }, body: text }),
  plan: (id: string) => request<Plan & { revision: number; approvalCodes: string[]; approved: boolean }>(`${ws(id)}/plan`),
  approve: (id: string, revision: number, codes: string[]) =>
    request<unknown>(`${ws(id)}/approvals`, json("POST", { revision, approve: codes })),

  environments: (id: string) => request<Environment[]>(`${ws(id)}/environments?refresh=true`),
  provision: (id: string) => request<{ environments: Environment[] }>(`${ws(id)}/environments`, json("POST", {})),
  start: (id: string, e: string) => request<Environment>(`${env(id, e)}/start`, { method: "POST" }),
  stop: (id: string, e: string) => request<Environment>(`${env(id, e)}/stop`, { method: "POST" }),
  destroy: (id: string, e: string, keepData: boolean) =>
    request<Environment>(`${env(id, e)}?keep_workspace=${keepData}`, { method: "DELETE" }),
  terminalTicket: (id: string, e: string) =>
    request<{ ticket: string; websocketPath: string }>(`${env(id, e)}/terminal`, { method: "POST" }),

  files: (id: string, e: string, path: string) =>
    request<{ path: string; entries: FileEntry[] }>(`${env(id, e)}/files?path=${enc(path)}`),
  fileUrl: (id: string, e: string, path: string) => `${env(id, e)}/files/content?path=${enc(path)}`,
  upload: (id: string, e: string, path: string, data: Blob) =>
    request<unknown>(`${env(id, e)}/files/content?path=${enc(path)}`, { method: "PUT", body: data }),
  deleteFile: (id: string, e: string, path: string, recursive: boolean) =>
    request<void>(`${env(id, e)}/files?path=${enc(path)}&recursive=${recursive}`, { method: "DELETE" }),

  snapshots: (id: string) => request<Snapshot[]>(`${ws(id)}/snapshots`),
  snapshot: (id: string, e: string, label: string) => request<Snapshot>(`${env(id, e)}/snapshots`, json("POST", { label })),
  restore: (id: string, e: string, snapshotId: string) => request<unknown>(`${env(id, e)}/restore`, json("POST", { snapshotId })),
  deleteSnapshot: (id: string, s: string) => request<void>(`${ws(id)}/snapshots/${enc(s)}`, { method: "DELETE" }),
  exportUrl: (id: string) => `${ws(id)}/export`,
};

/** Download a URL that needs the API token, without putting the token in the URL. */
export async function download(url: string, filename: string): Promise<void> {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(url, { headers });
  if (!res.ok) {
    const body = await res.json().catch(() => res.statusText);
    throw new ApiError(res.status, describeError(body, res.statusText), body);
  }
  const blobUrl = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
