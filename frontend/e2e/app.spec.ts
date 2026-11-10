import { expect, test, type Page } from "@playwright/test";

const BROKEN = `apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: e2e-lab}
spec:
  environments:
    - name: shell
      baseImage: plan9
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
`;
// No tool packs, so no package downloads are needed to build the image.
const GOOD = BROKEN.replace("plan9", "debian-12");

/** Fail the test on script errors, Content-Security-Policy violations and server errors. */
function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    // Rejected requests the test makes on purpose (e.g. saving an invalid manifest -> 422)
    // are logged by the browser; anything else, including 5xx and CSP violations, fails.
    if (m.type() === "error" && !/status of 4\d\d/.test(m.text())) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

async function createWorkspace(page: Page, name: string) {
  await page.goto("/app/");
  await page.getByLabel("New workspace name").fill(name);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
}

async function saveManifest(page: Page, text: string) {
  await page.getByLabel("Manifest YAML").fill(text);
  await page.getByRole("button", { name: "Save" }).click();
}

test("dashboard, workspace setup with validation, tool universe", async ({ page }) => {
  const errors = watchConsole(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/app\/$/);
  await expect(page.getByRole("heading", { name: "Your universe" })).toBeVisible();
  await expect(page.getByRole("region", { name: "System status" })).toContainText(/CONNECTED|NOT CONNECTED/);
  await expect(page.getByRole("region", { name: "Usage" })).toContainText("Environments");

  await createWorkspace(page, "e2e-basic");
  // A starter manifest is offered for a new workspace.
  await expect(page.getByLabel("Manifest YAML")).toHaveValue(/kind: Universe/);

  await page.getByLabel("Manifest YAML").fill(BROKEN);
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByRole("region", { name: "Check result" })).toContainText("unknown base image 'plan9'");
  await saveManifest(page, BROKEN);
  await expect(page.getByRole("alert")).toContainText("Not saved");

  await saveManifest(page, GOOD);
  await expect(page.getByText("Saved as revision 1.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Check result" })).toContainText("valid");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Workspaces" }).click();
  await expect(page.getByRole("link", { name: "e2e-basic" })).toBeVisible();
  await expect(page.getByText("revision 1")).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Tool universe" }).click();
  await expect(page.getByRole("region", { name: "Digital Forensics" })).toContainText("forensics");
  await expect(page.getByText("not installable yet").first()).toBeVisible();

  expect(errors).toEqual([]);
});

test("beginner form builds a valid manifest", async ({ page }) => {
  const errors = watchConsole(page);
  await createWorkspace(page, "e2e-form");
  await page.getByRole("button", { name: "Build with a form" }).click();
  const form = page.getByRole("region", { name: "Build with a form" });

  await form.getByLabel("Lab name").fill("Bad Name");
  await expect(form.getByRole("list", { name: "Fix these first" })).toContainText("Lab name");
  await expect(form.getByRole("button", { name: "Use this manifest" })).toBeDisabled();
  await form.getByLabel("Lab name").fill("web-practice");

  const machine = form.getByRole("group", { name: "Machine 1" });
  await machine.getByLabel("Operating system").selectOption("ubuntu-24-04");
  await machine.getByRole("checkbox", { name: /crypto/ }).check();
  await machine.getByRole("checkbox", { name: /web-appsec/ }).check(); // Ubuntu-only pack
  await machine.getByLabel("Size", { exact: true }).selectOption("medium");
  await machine.getByLabel("Network", { exact: true }).selectOption("restricted-egress");
  await expect(form.getByRole("list", { name: "Fix these first" })).toContainText("at least one website");
  await machine.getByLabel("Websites it may reach").fill("ghcr.io, mirror.gcr.io");
  await machine.getByLabel("Time limit hours").fill("8");

  await form.getByRole("button", { name: "Use this manifest" }).click();
  // Back in the editor with the generated YAML, already checked by the server.
  const editor = page.getByLabel("Manifest YAML");
  await expect(editor).toHaveValue(/toolPacks: \["crypto", "web-appsec"\]/);
  await expect(editor).toHaveValue(/allowedEgress: \["ghcr.io", "mirror.gcr.io"\]/);
  await expect(page.getByRole("region", { name: "Check result" })).toContainText("valid");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved as revision 1.")).toBeVisible();
  expect(errors).toEqual([]);
});

test("full lab: environment, terminal, files, snapshot, destroy", async ({ page }) => {
  test.skip(process.env.E2E_DOCKER !== "1", "set E2E_DOCKER=1 to run against a real Docker Engine");
  const errors = watchConsole(page);
  await createWorkspace(page, "e2e-full");
  await saveManifest(page, GOOD);
  await expect(page.getByText("Saved as revision 1.")).toBeVisible();

  await page.getByRole("button", { name: "Environments" }).click();
  await page.getByRole("button", { name: "Create environments" }).click();
  const envs = page.getByRole("region", { name: "Environments" });
  await expect(envs.getByText("running")).toBeVisible({ timeout: 120_000 });

  // Terminal: run a command in the real container.
  await envs.getByRole("button", { name: "Terminal" }).click();
  const term = page.getByRole("region", { name: "Terminal: shell" });
  await expect(term.getByText("connected")).toBeVisible();
  await page.getByTestId("terminal").click();
  await page.keyboard.type("echo e2e-$((6*7)) > /workspace/from-terminal.txt; whoami\n");
  await expect(page.locator(".xterm-rows")).toContainText("cyberlab");

  // Files: the terminal's file is visible, and an uploaded file appears.
  await page.getByRole("button", { name: "Files" }).click();
  const files = page.getByRole("region", { name: "Files" });
  await expect(files.getByText("from-terminal.txt")).toBeVisible();
  await files.locator('input[type="file"]').setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(files.getByText("notes.txt")).toBeVisible();

  // Snapshot.
  await page.getByRole("button", { name: "Snapshots" }).click();
  const snaps = page.getByRole("region", { name: "Snapshots" });
  await snaps.getByLabel("Snapshot label").fill("before cleanup");
  await snaps.getByRole("button", { name: "Save snapshot" }).click();
  await expect(snaps.getByText("before cleanup")).toBeVisible({ timeout: 60_000 });

  // Destroy (two-step confirmation), deleting its files.
  await page.getByRole("button", { name: "Environments" }).click();
  await envs.getByLabel(/keep files/).uncheck();
  await envs.getByRole("button", { name: "Destroy" }).click();
  await envs.getByRole("button", { name: "Confirm destroy" }).click();
  await expect(envs.getByText("No environments.")).toBeVisible();

  expect(errors).toEqual([]);
});
