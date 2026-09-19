# Meichu Hackathon — AMD Team 5

This file governs only Enoch and the AI agents assisting him. It does not define how the other three teammates must work. Its purpose is to help Enoch deliver a stable, reproducible contribution that can be integrated through GitHub into an AMD **Physical AI is Everywhere** project under severe time constraints.

## 1. Two-window workflow

Keep planning and implementation in separate Codex conversations.

### Communication window

- Acts as architecture coach and work coordinator.
- Clarifies the goal, priorities, data flow, system boundaries, risks, and the next smallest valuable milestone.
- Explains decisions in concise Traditional Chinese and assumes the user is still learning Git, AI deployment, Linux, and hardware workflows.
- Produces short, bounded prompts that can be pasted into the work window.
- Reviews the work window's report, decides whether the result is acceptable, and chooses the next task.
- Does not edit files, run deployments, commit, push, create branches, or operate production services unless the user explicitly changes this window's role.

### Work window

- Reads this file and the repository's current documentation before acting.
- Implements the exact assigned scope with high autonomy.
- May make low-risk, reversible technical decisions without asking step by step.
- Does not expand product scope, redesign the architecture, or assign work to teammates.
- Verifies only what is proportionate to the task and returns a concise evidence-based report.

The repository is the durable source of truth. Chat history is supporting context, not authoritative project state. If the two windows disagree, inspect the repository and ask only when the difference changes product direction or creates material risk.

## 2. Non-negotiable competition gates

The design and implementation must clearly demonstrate all of the following:

1. AMD Instinct MI300 is actually used for model training, fine-tuning, or substantial data processing.
2. The PN54 AI PC performs local inference or another essential computation.
3. A camera, microphone, sensor, or device captures information from the physical world.
4. The AI decision produces a visible, audible, or actionable real-world response; the result cannot be only a chat interface or cloud API call.
5. The system includes at least one of: model fine-tuning or RAG.
6. The code is published on GitHub with an English, reproducible `README.md`.
7. A public video shows the complete system running.

If the proposal cannot answer **what MI300 does, what PN54 does, how the system senses the world, how it responds, and where fine-tuning or RAG occurs**, stop adding features and repair the core path first.

## 3. Autonomy and scope

- Execute low-risk, reversible, in-scope technical decisions directly.
- Ask only when a decision affects product direction, teammate ownership, paid services, accounts or permissions, secret transfer, destructive operations, major environment changes, or an unauthorized push, PR, merge, or deployment.
- Do not decide another teammate's responsibilities or change an area known to be owned by a teammate without coordination.
- Do not let a minor edge case block the core flow. Record it and continue with the highest-priority work.
- Add a dependency only when it is necessary or saves substantial implementation time, code, or tokens. Prefer official and actively maintained sources.
- Do not replace the core architecture, create another web app or backend, or add an unnecessary container unless the task requires it.

## 4. Priority levels and time modes

- `P0 — Required`: eligibility gates and the core vertical flow needed for a 2–3 minute demo.
- `S0 — Safety`: secrets, authorization, destructive data behavior, and risks that can break the demo or shared environment.
- `P1 — Needed`: reliability, basic error handling, restart recovery, essential UI, and measurements.
- `P2 — Bonus`: extra models, advanced hardware feedback, visual polish, and optional automation.

The user will report the remaining competition time. Adapt automatically:

- **Build:** complete P0 end to end; do not optimize appearance prematurely.
- **Stabilize:** freeze architecture, integrate components, measure performance, and resolve S0 plus critical P1 issues.
- **Ship:** add no new features; focus on GitHub, README, video, slides, QA, and a fallback demo.

While P0 is incomplete, do not add a second model, rebuild the interface, or start P2 work.

## 5. System boundaries

```text
Camera / microphone / sensor
              ↓
PN54: physical input, local real-time inference, UI, and physical feedback
              ↑
GitHub: source code, configuration, documentation, and version history
              ↑
Manta / MLSteam / MI300: training, fine-tuning, data processing, and experiments
```

- GitHub is the source-of-truth and collaboration hub, not a compute environment.
- Never assume Manta and PN54 share the same packages, paths, OS, runtime, or hardware capabilities.
- Do not commit large weights, datasets, caches, or training outputs. Record their source, license, version, checksum, output location, and retrieval method.
- Any hardware or startup step that cannot be automated must be documented clearly in the README.

### Current MVP and extension boundary

The current product is a privacy-first assistant for a shared refrigerator. Keep the MVP limited to this flow:

```text
User selects put-in or take-out
              ↓
One external camera captures the user and one handheld item
              ↓
PN54 runs face identity and item recognition in parallel
              ↓
Bind user_id + item_id + selected action to one session
              ↓
Create or query ownership → screen and audio feedback
```

- P0 supports one person and one visible item per interaction. The user explicitly selects `PUT_IN` or `TAKE_OUT`; AI action recognition is not part of the MVP.
- Use an off-the-shelf face model locally. Do not send face images or face embeddings to MI300, and do not retain continuous video by default.
- MI300 fine-tunes the item-instance embedding model; PN54 runs the exported model locally and records measured latency, FPS, and compute device.
- A new item is enrolled with multiple clear views and bound to the current `user_id`. A take-out operation matches `item_id`, queries `owner_id` and sharing policy, then allows, warns, or returns unknown.
- Automatic put-in/take-out recognition, expiry OCR, food-state estimation, RAG, voice interaction, notifications, and multi-person or multi-item scenes are post-MVP extensions.

Keep the implementation modular but small:

```text
src/fridge_guardian/domain/       stable entities and enums
src/fridge_guardian/contracts/    replaceable interfaces
src/fridge_guardian/application/  session orchestration and ownership rules
src/fridge_guardian/adapters/     camera, models, SQLite, UI, and audio
training/                         MI300 data, fine-tuning, evaluation, and export
```

- Define replaceable contracts for `ActionSource`, `IdentityProvider`, `ItemRecognizer`, repositories, and feedback. The MVP uses `ManualActionSource`; a future `VisionActionSource` must be swappable without changing ownership logic.
- Modules exchange typed results carrying `session_id`, identifiers, confidence, and timestamps. Model adapters must not call each other directly; the application coordinator combines their results.
- Treat `domain/` and `contracts/` as shared integration boundaries. Coordinate changes there; keep model-, UI-, training-, and storage-specific work inside their own modules.
- Add future extension points only when implementing them. Do not create empty RAG, expiry, voice, or action-recognition subsystems during P0.

## 6. Minimum delivery loop

1. State the user, problem, physical input, AI decision, and real-world response in one short description.
2. Define the 2–3 minute MVP demo and its observable success condition.
3. First complete `sensor input → PN54 inference → response`.
4. Complete the smallest valid MI300 fine-tune or required data-processing job and preserve evidence.
5. Export the artifact, deploy it on PN54, and measure actual latency, FPS, and compute device.
6. Fix only integration issues that block the core flow before considering P1 or P2 work.
7. Start the README, architecture diagram, video script, slides, and QA notes early.

Before implementation, confirm the assigned scope, expected deliverable, allowed files or systems, minimum verification, and stopping condition.

## 7. Minimum testing and evidence

- Keep one complete happy-path demo test for P0.
- For S0, test only the highest-risk authorization, secret-handling, or data-integrity cases.
- Prefer unit tests or focused manual verification for P1; do not add E2E tests by default.
- Test P2 only when time permits.
- Run the main flow successfully at least three consecutive times on the final demo environment.
- Label evidence accurately as static, mocked, local, Manta, PN54, or production. One category never proves another.
- When a failure occurs, identify the failing layer—device input, preprocessing, model, runtime, API, UI, or external service—before modifying the system.
- If blocked, use the lowest-cost valid verification or fallback. Never add a production test backdoor.

## 8. GitHub collaboration

- Default flow: update from `main` → create a feature branch → implement → run minimum verification → commit → push → open a PR.
- The work agent may commit, push, and open a PR when the assigned task authorizes that workflow. A designated teammate or repository owner decides whether to merge.
- When `main` changes, integrate it into the existing feature branch; do not discard and rebuild completed work.
- Before editing, inspect the current branch, working tree, and remote state. Preserve teammate changes and unrelated local work.
- Keep each commit and PR focused on one purpose. Exclude unrelated formatting, generated artifacts, model files, and temporary output.
- Never force-push shared history, rewrite shared commits, or merge a PR without explicit authority.
- Never commit `.env` files, access tokens, passwords, SSH private keys, platform credentials, personal data, or unauthorized datasets.

## 9. Environment constraints

### Windows development machine

- Used for Codex, VS Code, Git, documentation, and general development.
- The local SSH alias is `mlsteam-amd`.
- Never commit or upload the SSH configuration or private key, and never paste them into documentation.

### Manta / MLSteam / MI300

- Verified platform: PyTorch 2.9.1, ROCm 7.1, AMD Instinct MI300X, with GPU access through PyTorch.
- Use `/opt/venv/bin/python` for AI Python and `/opt/venv/bin/pip` for its packages.
- Do not use `/usr/bin/python` or `/usr/bin/python3` to determine whether the platform AI environment is available.
- Persistent storage is `/mlsteam/workspace`.
- After a Lab restart, first run `bash /mlsteam/workspace/init.sh` in the MLSteam web terminal.
- If SSH fails, check that the Lab is running, SSH is enabled, and the current `22/tcp` port forward is correct before changing keys.
- When the GPU is no longer needed, confirm that results are persisted and then stop the Lab.

Before AI work, run:

```bash
pwd
/opt/venv/bin/python --version
/opt/venv/bin/python -c "import torch; print(torch.__version__); print(torch.version.hip); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'No GPU')"
git status --short --branch
```

### PN54

- Known environment: Ubuntu 24.04.4, kernel 7.0.0-28-generic, Ryzen AI 7 350, Radeon 860M.
- PN54 is the deployment environment. Do not assume it contains Manta's PyTorch or ROCm stack.
- Do not reinstall, upgrade, or downgrade ROCm, the NPU runtime, drivers, or kernel until compatibility is verified and a recovery path exists.
- Prefer reproducible, low-risk deployment formats. Select CPU, Vulkan, iGPU, or NPU based on measurement, and retain a fallback that can complete the demo.
- Evaluate Lemonade separately from the vision-model runtime. Installing Lemonade does not prove that another model has hardware acceleration.

### General environment safety

- Before installing anything, confirm the active Python, virtual environment, framework, and hardware state.
- Do not run `pip install torch`, `pip install --upgrade torch`, `apt upgrade`, or another command that can replace the platform's core runtime unless the exact version, impact, and recovery path are known.
- Keep project dependencies separate from the platform PyTorch and ROCm stack. Record added packages in a requirements file or lockfile.
- Store secrets in environment variables and ensure `.env` files are ignored by Git.
- Explain the impact and obtain confirmation before deletion, overwrite, large downloads, core-runtime installation, or long GPU jobs.

## 10. Records and concise reporting

Preserve:

- Successful commands, package and model versions, data sources, licenses, and known limitations.
- MI300 configuration, training metrics, duration, output location, and demonstrable evidence.
- PN54 startup procedure, runtime, latency or FPS, compute device, and restart recovery procedure.
- The shortest procedure a teammate can follow to run the contribution.

During the competition, the work window reports only:

1. Outcome.
2. Action required from the user or teammates.
3. Essential verification and the environment where it ran.
4. Major risk or blocker.
5. Branch, commit, and PR status.
6. The next highest-priority task.

Do not provide step-by-step teaching or a long file-by-file diary in the work window. Interrupt only for a material decision or external action that the agent cannot safely perform.

## 11. README and submission artifacts

The English `README.md` must include:

- The problem, target users, project summary, and demo scenario.
- A hardware and software architecture diagram showing the roles of MI300, GitHub, and PN54.
- Data sources, licenses, the fine-tuning or RAG method, and evaluation results.
- Separate setup and execution instructions for Manta training and PN54 inference.
- Model source, version, checksum, and hardware wiring or device setup.
- Inputs, outputs, latency or FPS, known limitations, and troubleshooting.
- The shortest reproducible path for a new contributor.

The final submission must also include public source code, a public full-run video, presentation slides, QA notes, and a fallback demo for the core flow.

## 12. Definition of done

Report work as complete only when:

- The deliverable exists in the correct working tree and no teammate work was overwritten.
- The shortest repeatable command or operating procedure is documented.
- Verification matches the task's priority, and the machine or environment is named.
- No secrets, large temporary artifacts, or data that cannot legally be published were committed.
- When sharing is required, the work is committed, pushed, and submitted as a PR; the agent does not merge it without authority.
- Any MI300 or PN54 dependency has recorded environment details, versions, measurements, and reproduction evidence.
- A viewer can understand `physical sensing → AI decision → real-world response` within a 2–3 minute demo.
