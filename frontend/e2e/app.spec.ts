import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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

test("describe your lab: plain words to a saved manifest", async ({ page }) => {
  const errors = watchConsole(page);
  await createWorkspace(page, "e2e-describe");
  await page.getByRole("button", { name: "Describe your lab" }).click();
  const panel = page.getByRole("region", { name: "Describe your lab" });
  await expect(panel).toContainText("built-in planner");

  // Attacks on real systems are refused, before anything is drafted.
  await panel.getByLabel("What do you want to practise?").fill("hack facebook.com");
  await panel.getByRole("button", { name: "Draft my lab" }).click();
  await expect(panel.getByRole("alert", { name: "Not drafted" })).toContainText("written permission");

  await panel.getByRole("button", { name: /attacker and a target/ }).click(); // an example
  await panel.getByRole("button", { name: "Draft my lab" }).click();
  const draft = panel.getByRole("group", { name: "Draft" });
  await expect(draft).toContainText("passes checks");
  await expect(draft.getByLabel("Draft manifest")).toContainText("baseImage: parrot-core");
  await expect(draft.getByLabel("Draft manifest")).toContainText("name: target");
  await draft.getByRole("button", { name: "Use this draft" }).click();

  // Only the editor is filled in; the user still saves it.
  await expect(page.getByLabel("Manifest YAML")).toHaveValue(/web-appsec/);
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

  // Troubleshooter: plain-language health check.
  await envs.getByRole("button", { name: "What's wrong?" }).click();
  const help = page.getByRole("region", { name: "What's wrong: shell" });
  await expect(help.getByRole("list", { name: "Advice" })).toContainText("Running normally");
  await help.getByRole("button", { name: "Close" }).click();

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

test("labs catalog: browse, and a clear message when no machines can be created", async ({ page }) => {
  test.skip(process.env.E2E_DOCKER === "1", "covered by the full ready-made lab test");
  const errors = watchConsole(page);
  await page.goto("/app/");
  await page.getByRole("link", { name: "Browse labs →" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Ready-made labs" })).toBeVisible();
  const card = page.getByRole("region", { name: "First steps in Linux" });
  await expect(card.getByText("beginner")).toBeVisible();
  await expect(card.getByText(/4 tasks/)).toBeVisible();
  // Filters: advanced labs only, then defence only.
  await page.getByLabel("Level").selectOption("advanced");
  await expect(page.getByRole("region", { name: /Recon chain/ })).toBeVisible();
  await expect(card).toHaveCount(0);
  await page.getByLabel("Level").selectOption("all");
  await page.getByLabel("Topic").selectOption("blue-team");
  await expect(page.getByRole("region", { name: /Log detective/ })).toBeVisible();
  await expect(card).toHaveCount(0);
  await page.getByLabel("Topic").selectOption("all");
  await expect(page.getByLabel("Your progress")).toContainText("0 of");
  await card.getByRole("button", { name: "Start lab" }).click();
  await expect(page.getByRole("alert")).toContainText("no execution provider");
  expect(errors.filter((e) => !/status of 503/.test(e))).toEqual([]);
});

test("ready-made lab: one click, read a flag in the terminal, get it checked", async ({ page }) => {
  test.skip(process.env.E2E_DOCKER !== "1", "set E2E_DOCKER=1 to run against a real Docker Engine");
  const errors = watchConsole(page);
  await page.goto("/app/#/labs");
  await page.getByRole("region", { name: "First steps in Linux" }).getByRole("button", { name: "Start lab" }).click();
  const lab = page.getByRole("region", { name: "Lab", exact: true });
  await expect(lab.getByText("0 of 4 solved")).toBeVisible({ timeout: 180_000 });

  // A wrong answer is refused, and hints appear one at a time.
  const task = page.getByRole("region", { name: "Task 1: Read a file" });
  await task.getByLabel("Answer for Read a file").fill("CL{not-it}");
  await task.getByRole("button", { name: "Check" }).click();
  await expect(task.getByRole("alert")).toContainText("Not quite");
  await task.getByRole("button", { name: "Show a hint" }).click();
  await expect(task.getByText(/lists files/)).toBeVisible();

  // Read the real flag with cat in the real machine, and submit it.
  await lab.getByRole("button", { name: "Open terminal: student" }).click();
  await expect(page.getByRole("region", { name: "Terminal: student" }).getByText("connected")).toBeVisible();
  await page.getByTestId("terminal").click();
  await page.keyboard.type("cat welcome.txt\n");
  const rows = page.locator(".xterm-rows");
  await expect(rows).toContainText(/CL\{[0-9a-f]{16}\}/);
  const flag = ((await rows.innerText()).match(/CL\{[0-9a-f]{16}\}/) ?? [""])[0];
  await task.getByLabel("Answer for Read a file").fill(flag);
  await task.getByRole("button", { name: "Check" }).click();
  await expect(task.getByText("solved")).toBeVisible();
  await expect(lab.getByText("1 of 4 solved")).toBeVisible();

  // Clean up: destroy the lab machine and its files.
  await page.getByRole("button", { name: "Environments" }).click();
  const envs = page.getByRole("region", { name: "Environments" });
  await envs.getByLabel(/keep files/).uncheck();
  await envs.getByRole("button", { name: "Destroy" }).click();
  await envs.getByRole("button", { name: "Confirm destroy" }).click();
  await expect(envs.getByText("No environments.")).toBeVisible();
  expect(errors).toEqual([]);
});

test("classes: a teacher creates a class, gets a code, and opens or closes it", async ({ page }) => {
  const errors = watchConsole(page);
  await page.goto("/app/#/classes");
  const teach = page.getByRole("region", { name: "Teach a class" });
  const name = `Security 101 ${Date.now() % 100000}`;
  await teach.getByLabel("Class name").fill(name);
  await teach.getByLabel("Lab for the class").selectOption("log-detective");
  await teach.getByRole("button", { name: "Create class" }).click();
  const code = teach.getByLabel(`Code for ${name}`);
  await expect(code).toHaveText(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/);
  await teach.getByRole("link", { name }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  const students = page.getByRole("region", { name: "Students" });
  await expect(students.getByText(/No students yet/)).toBeVisible();
  await students.getByRole("button", { name: "Close to new students" }).click();
  await expect(students.getByRole("button", { name: "Open to new students" })).toBeVisible();
  // The teacher can't join their own class (students join with the code).
  await page.goto("/app/#/classes");
  const join = page.getByRole("region", { name: "Join a class" });
  await join.getByLabel("Class code").fill(await code.innerText());
  await join.getByRole("button", { name: "Join" }).click();
  await expect(page.getByRole("alert")).toContainText(/closed|you teach this class/);
  expect(errors.filter((e) => !/status of 409/.test(e))).toEqual([]);
});

test("write your own lab: import, share, download", async ({ page, request }) => {
  const errors = watchConsole(page);
  const title = `My note lab ${Date.now() % 100000}`;
  const lab = `id: my-note-lab
title: ${title}
level: beginner
minutes: 10
topic: linux
summary: A lab written in the browser test.
spec:
  environments:
    - name: box
      baseImage: ubuntu-24-04
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
      network: {mode: isolated}
setup:
  - {environment: box, path: note.txt, content: "flag: {{flag:read}}"}
tasks:
  - {id: read, title: Read it, text: "cat note.txt"}
`;
  await page.goto("/app/#/labs");
  const mine = page.getByRole("region", { name: "Your labs" });
  await mine.getByRole("button", { name: "Import a lab file" }).click();
  await mine.getByLabel("Lab YAML").fill(lab.replace("isolated", "internet"));
  await mine.getByRole("button", { name: "Check and import" }).click();
  await expect(mine.getByRole("alert")).toContainText("'isolated' or 'lab'"); // refused, with the reason
  await mine.getByLabel("Lab YAML").fill(lab);
  await mine.getByRole("button", { name: "Check and import" }).click();
  await expect(mine.getByText(title)).toBeVisible();
  await expect(mine.getByText("private")).toBeVisible();
  const card = page.getByRole("region", { name: title });
  await expect(card.getByText("yours")).toBeVisible();
  await mine.getByRole("button", { name: "Share" }).click();
  await expect(mine.getByText("shared with everyone")).toBeVisible();
  // the shared lab is in the public catalog, and its file can be downloaded
  const listed = (await (await request.get("/v1/labs")).json()) as { id: string; title: string; source: string }[];
  const shared = listed.find((l) => l.title === title)!;
  expect(shared.source).toBe("community");
  expect(await (await request.get(`/v1/labs/${shared.id}/file`)).text()).toContain(title);
  await mine.getByRole("button", { name: "Delete" }).click();
  await expect(mine.getByText(title)).toHaveCount(0);
  expect(errors.filter((e) => !/status of 422/.test(e))).toEqual([]);
});

const DESKTOP = `apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: e2e-desktop}
spec:
  environments:
    - name: gui
      baseImage: ubuntu-24-04
      resources: {cpu: 1, memoryMiB: 2048, storageGiB: 4}
      network: {mode: isolated}
      access: {desktop: true}
`;

test("remote desktop: see the screen and type into it", async ({ page }) => {
  test.skip(process.env.E2E_DOCKER !== "1", "set E2E_DOCKER=1 to run against a real Docker Engine");
  test.setTimeout(600_000); // the first desktop image build downloads packages
  const errors = watchConsole(page);
  await createWorkspace(page, `e2e-desktop-${Date.now() % 100000}`); // unique: safe to repeat
  await saveManifest(page, DESKTOP);
  await expect(page.getByText("Saved as revision 1.")).toBeVisible();

  await page.getByRole("button", { name: "Environments" }).click();
  await page.getByRole("button", { name: "Create environments" }).click();
  const envs = page.getByRole("region", { name: "Environments" });
  await expect(envs.getByText("running")).toBeVisible({ timeout: 540_000 });

  await envs.getByRole("button", { name: "Desktop" }).click();
  const desk = page.getByRole("region", { name: "Desktop: gui" });
  await expect(desk.getByText("connected")).toBeVisible({ timeout: 30_000 });
  const canvas = page.getByTestId("desktop").locator("canvas");
  await expect(canvas).toBeVisible();

  // Something is drawn: the xterm window on the dark background, not one flat colour.
  await expect
    .poll(async () =>
      canvas.evaluate((c: HTMLCanvasElement) => {
        const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
        const seen = new Set<number>();
        for (let i = 0; i < d.length; i += 4 * 97) seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
        return seen.size;
      }), { timeout: 20_000 })
    .toBeGreaterThan(2);

  // Type into the desktop's terminal window (xterm opens at the top left, 100x30 characters).
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.2, box.y + box.height * 0.2);
  await page.keyboard.type("echo typed-on-desktop > /workspace/from-desktop.txt\n", { delay: 20 });

  // Clipboard, browser -> desktop: send a command, paste it into the terminal with a middle-click.
  await desk.getByLabel("Clipboard").fill("echo pasted-from-browser > /workspace/from-clipboard.txt\n");
  await desk.getByRole("button", { name: "Send to desktop" }).click();
  await expect(desk.getByRole("status")).toContainText("Sent");
  const b1 = (await canvas.boundingBox())!; // measure again: the page may have scrolled
  await page.mouse.click(b1.x + b1.width * 0.2, b1.y + b1.height * 0.2, { button: "middle" });

  // Clipboard, desktop -> browser: print a line, select it with a triple-click (xterm copies
  // selections), and it appears in the clipboard box.
  // The paste arrives a moment later (the terminal asks the VNC server for the text); a
  // person never types that fast, but a test does, so let it finish first.
  await page.waitForTimeout(1500);
  // A middle-click doesn't move keyboard focus to the desktop; a left-click does.
  await page.mouse.click(b1.x + b1.width * 0.2, b1.y + b1.height * 0.2);
  await page.keyboard.type("clear; echo ZZ-COPY-ME-42\n", { delay: 20 });
  // The screen redraws a moment later, so retry until the selected line is the new one.
  await expect(async () => {
    const b2 = (await canvas.boundingBox())!;
    // framebuffer point (80, 68): the first text line of the xterm window at +40+40
    await page.mouse.click(b2.x + (b2.width * 80) / 1280, b2.y + (b2.height * 68) / 800, { clickCount: 3 });
    await expect(desk.getByLabel("Clipboard")).toHaveValue(/ZZ-COPY-ME-42/, { timeout: 2_000 });
  }).toPass({ timeout: 20_000 });

  await desk.getByRole("button", { name: "Close desktop" }).click();
  await page.getByRole("button", { name: "Files" }).click();
  const files = page.getByRole("region", { name: "Files" });
  await expect(files.getByText("from-desktop.txt")).toBeVisible({ timeout: 15_000 });
  await expect(files.getByText("from-clipboard.txt")).toBeVisible();

  await page.getByRole("button", { name: "Environments" }).click();
  await envs.getByLabel(/keep files/).uncheck(); // leave nothing behind
  await envs.getByRole("button", { name: "Destroy" }).click();
  await envs.getByRole("button", { name: "Confirm destroy" }).click();
  await expect(envs.getByText("No environments.")).toBeVisible();
  expect(errors).toEqual([]);
});

/** A real, tiny .deb (built with dpkg-deb in a container) that installs `cyberlab-hello`. */
function buildTestDeb(): Buffer {
  const dir = mkdtempSync(join(tmpdir(), "cyberlab-deb-"));
  const mirror = process.env.E2E_IMAGE_MIRROR ?? "docker.io";
  const script =
    "set -e; mkdir -p /p/DEBIAN /p/usr/local/bin; " +
    "printf 'Package: cyberlab-hello\\nVersion: 1.0\\nArchitecture: all\\nMaintainer: t <t@example.com>\\nDescription: test\\n' > /p/DEBIAN/control; " +
    "printf '#!/bin/sh\\necho hello-from-my-package\\n' > /p/usr/local/bin/cyberlab-hello; " +
    "chmod 755 /p/usr/local/bin/cyberlab-hello; dpkg-deb --build /p /out/hello.deb; chmod 644 /out/hello.deb";
  execFileSync("docker", ["run", "--rm", "-v", `${dir}:/out`, `${mirror}/library/debian:12-slim`, "sh", "-c", script]);
  return readFileSync(join(dir, "hello.deb"));
}

test("install your own package (.deb) from the Files tab", async ({ page }) => {
  test.skip(process.env.E2E_DOCKER !== "1", "set E2E_DOCKER=1 to run against a real Docker Engine");
  test.setTimeout(300_000);
  const errors = watchConsole(page);
  await createWorkspace(page, `e2e-install-${Date.now() % 100000}`);
  await saveManifest(page, GOOD);
  await expect(page.getByText("Saved as revision 1.")).toBeVisible();
  await page.getByRole("button", { name: "Environments" }).click();
  await page.getByRole("button", { name: "Create environments" }).click();
  const envs = page.getByRole("region", { name: "Environments" });
  await expect(envs.getByText("running")).toBeVisible({ timeout: 120_000 });

  await page.getByRole("button", { name: "Files" }).click();
  const files = page.getByRole("region", { name: "Files" });
  await files.locator('input[type="file"]').setInputFiles({ name: "hello.deb", mimeType: "application/vnd.debian.binary-package", buffer: buildTestDeb() });
  await expect(files.getByText("hello.deb")).toBeVisible();
  await files.getByRole("button", { name: "Install" }).click();
  await files.getByRole("button", { name: "Install (restarts the machine)" }).click();
  await expect(files.getByLabel("Installed packages")).toContainText("hello.deb", { timeout: 240_000 });

  // The machine was restarted with the package: run it from the terminal.
  await page.getByRole("button", { name: "Environments" }).click();
  await envs.getByRole("button", { name: "Terminal" }).click();
  const term = page.getByRole("region", { name: "Terminal: shell" });
  await expect(term.getByText("connected")).toBeVisible();
  await page.getByTestId("terminal").click();
  await page.keyboard.type("cyberlab-hello\n");
  await expect(page.locator(".xterm-rows")).toContainText("hello-from-my-package");
  await term.getByRole("button", { name: "Close terminal" }).click();

  await envs.getByLabel(/keep files/).uncheck(); // leave nothing behind
  await envs.getByRole("button", { name: "Destroy" }).click();
  await envs.getByRole("button", { name: "Confirm destroy" }).click();
  await expect(envs.getByText("No environments.")).toBeVisible();
  expect(errors).toEqual([]);
});

test.describe("real sign-in (Supabase Auth stand-in, ES256 tokens)", () => {
  const port = Number(process.env.E2E_PORT ?? 8799);
  const app = `http://127.0.0.1:${port - 2}/app/`;
  const auth = `http://127.0.0.1:${port - 3}`;

  test("create an account, confirm, sign in, use CyberLab, sign out", async ({ page, request }) => {
    const errors = watchConsole(page);
    const email = `learner-${Date.now()}@example.com`;
    await page.goto(app);
    const form = page.getByRole("region", { name: "Sign in" });
    await expect(form).toBeVisible();

    // Wrong password: a plain message.
    await form.getByLabel("Email").fill(email);
    await form.getByLabel("Password").fill("nope-nope");
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText("Wrong email or password.");

    // Create an account: a confirmation email is "sent".
    await page.getByRole("button", { name: "Create an account" }).click();
    const create = page.getByRole("region", { name: "Create account" });
    await create.getByLabel("Email").fill(email);
    await create.getByLabel("Password").fill("a-long-password");
    await create.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByRole("status")).toContainText("confirmation link");

    // Signing in before confirming explains what to do.
    await form.getByLabel("Password").fill("a-long-password");
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText("confirm your email");

    // "Click the link in the email", then sign in for real.
    expect((await request.post(`${auth}/__confirm`, { data: { email } })).ok()).toBe(true);
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByLabel("Signed in as")).toHaveText(email);

    // The API accepts the ES256 token (checked against the JWKS): create a workspace.
    await page.getByLabel("New workspace name").fill("signed-in-lab");
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "signed-in-lab" })).toBeVisible();

    // Still signed in after a reload (this tab only).
    await page.reload();
    await expect(page.getByLabel("Signed in as")).toHaveText(email);

    // Sign out: back to the sign-in screen, and the API refuses requests without a token.
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("region", { name: "Sign in" })).toBeVisible();
    expect((await request.get(`http://127.0.0.1:${port - 2}/v1/workspaces`)).status()).toBe(401);

    // Forgot the password: ask for a reset email, follow its link, choose a new password.
    await page.getByRole("button", { name: "Forgot password?" }).click();
    const reset = page.getByRole("region", { name: "Reset password" });
    await reset.getByLabel("Email").fill(email);
    await reset.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.getByRole("status")).toContainText("link to choose a new password");
    const { link } = await (await request.get(`${auth}/__reset_link?email=${encodeURIComponent(email)}`)).json();
    expect(link.startsWith(app)).toBe(true); // the email brings people back to this CyberLab
    await page.goto(link);
    const choose = page.getByRole("region", { name: "Choose a new password" });
    await choose.getByLabel("New password", { exact: true }).fill("a-new-long-password");
    await choose.getByLabel("New password again").fill("a-different-one");
    await choose.getByRole("button", { name: "Save new password" }).click();
    await expect(page.getByRole("alert")).toContainText("don't match");
    await choose.getByLabel("New password again").fill("a-new-long-password");
    await choose.getByRole("button", { name: "Save new password" }).click();
    await expect(page.getByLabel("Signed in as")).toHaveText(email);
    expect(page.url()).not.toContain("access_token"); // the token is removed from the address

    // The old password no longer works; the new one does.
    await page.getByRole("button", { name: "Sign out" }).click();
    await form.getByLabel("Email").fill(email);
    await form.getByLabel("Password").fill("a-long-password");
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText("Wrong email or password.");
    await form.getByLabel("Password").fill("a-new-long-password");
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByLabel("Signed in as")).toHaveText(email);

    // An expired link explains itself instead of failing silently.
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.goto(`${app}#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired`);
    await expect(page.getByRole("alert")).toContainText("expired or was already used");
    expect(errors.filter((e) => !/status of 4\d\d/.test(e))).toEqual([]);
  });
});
