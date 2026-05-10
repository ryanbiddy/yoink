# Yoink Build Kit

> **Domain note:** This historical build doc references `yoink.video`. The current production URL is `ryanbiddy.com/yoink` (the `yoink.video` domain belongs to a different product). See `server.py`, `installer/yoink.iss`, or `docs/store-listing.md` for the current canonical URL.

This is a build orchestration kit that runs the v1 weekend build through Claude Code.

## What's in here

```
build-prompts/
  prompt-1-rebrand.md       — Project rebrand and repo setup
  prompt-2-corpus.md        — Metadata enrichment + corpus format rewrite
  prompt-3-destinations.md  — Two destination buttons + prompt library
  prompt-4-polish.md        — Error states, success copy, version bump
  prompt-5-store-assets.md  — Chrome Web Store assets and listing draft
  prompt-6-commit.md        — Final smoke test, commit, prepare folder rename

run-build.ps1               — PowerShell orchestrator that runs all 6 prompts in order
HOW_TO_USE.md               — This file
```

## How to use

### 1. Drop these files into your project folder

Copy the entire contents of this kit into your project root:

```
C:\Users\hello\OneDrive\Desktop\yt-extractor\
  build-prompts\
    prompt-1-rebrand.md
    prompt-2-corpus.md
    ...
  run-build.ps1
  HOW_TO_USE.md
```

### 2. Edit prompt-1-rebrand.md to add your GitHub username

Open `build-prompts/prompt-1-rebrand.md` and search for `REPLACE_WITH_YOUR_USERNAME`. Replace it with your actual GitHub username (the one you'll create the `yoink` repo under). The orchestrator will pass the file as-is to Claude Code, so this needs to be done before you start.

There's a similar placeholder in `prompt-6-commit.md` if you want to update the README example URL there too — optional, since Claude Code can ask if needed.

### 3. Tier 0 prep (do these in a browser before running the orchestrator)

1. Buy `yoink.video` on Cloudflare Registrar
2. Create the GitHub repo at `github.com/<your-username>/yoink` (public, MIT license, README checked)
3. Reserve `@yoinkvideo` on X
4. Generate the logo with Recraft / Midjourney and save to `assets/logo.png` and `assets/logo.svg` in your project folder
5. (Optional) Spin up `replayryan.com` placeholder via Vercel
6. (Optional) Spin up a basic placeholder at `yoink.video` via Vercel

### 4. Run the orchestrator

Open PowerShell in the project folder and run:

```powershell
cd C:\Users\hello\OneDrive\Desktop\yt-extractor
.\run-build.ps1
```

The first time PowerShell may complain about execution policy. If so, run this once in the same terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then re-run `.\run-build.ps1`.

### 5. What the orchestrator does

For each of the 6 prompts:

1. Shows you the prompt name and waits for your "go"
2. Pipes the prompt file into Claude Code
3. Claude Code runs and exits when done
4. The orchestrator pauses with a smoke test checklist
5. You run the smoke test manually
6. Type `continue` to advance, `retry` to re-run the prompt, or `stop` to exit

After prompt 6 completes, the orchestrator offers to rename the parent folder from `yt-extractor` to `Yoink`. The rename happens in a new PowerShell process (because the current process can't release the folder lock while it's running).

### 6. After the rename

Your project lives at `C:\Users\hello\OneDrive\Desktop\Yoink\`.

Restart the Yoink server from the new location:

```powershell
cd C:\Users\hello\OneDrive\Desktop\Yoink
.\start_server.bat
```

The Chrome extension doesn't need any changes — it talks to the server via HTTP, not the file system.

## Pacing

The orchestrator is designed to run across two days, not one continuous session.

**Saturday:**
- Prompts 1, 2, 3 (the heavy lifting — rebrand, corpus rewrite, destinations)
- Stop after prompt 3, do a longer smoke test, sleep on it

**Sunday:**
- Prompts 4, 5, 6 (polish, store assets, commit)
- After prompt 6, the folder rename, then close everything

If you try to do all 6 in one day, the smoke testing gets sloppy and bugs slip through. The Sunday split is the right pace.

## If something goes wrong

- **Claude Code asks a clarifying question:** answer briefly and let it proceed. The prompts are designed to give it enough context, but it may have edge-case questions about your specific code.
- **A prompt fails or produces broken code:** type `retry` at the smoke test prompt to re-run it. Claude Code will see the partial state and fix forward.
- **You need to stop and resume later:** type `stop`. To resume, you'll re-run the script and it'll start from prompt 1 — you'll need to skip past the prompts you've already completed manually.
- **The folder rename fails:** the orchestrator prints fallback instructions. You can rename manually with `Rename-Item` after closing all programs that have files in the folder open.

## Don't forget

This is a build kit, not a launch kit. After all 6 prompts finish, you have a v1 build but you haven't shipped publicly yet. Next weekend's work:

- Inno Setup installer with bundled deps
- Demo video (60-90 sec)
- Chrome Web Store submission
- Final landing page copy at yoink.video

Public launch is 2-3 weekends out, gated on Web Store review approval.
