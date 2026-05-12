# setup.html copy revision draft

Goal: sharpen setup around "the YouTube layer for any AI agent" and "queryable corpus with operator-grade intelligence" without turning the page into marketing copy.

## Page title and lede - first install

Original:

> Let's get you set up.
>
> Two minutes. Then you'll be yoinking videos straight into Claude.

Proposed:

> Set up your local YouTube layer for AI.
>
> In a few minutes, Yoink can turn any YouTube video into a structured corpus your AI tools can actually use.

Rationale: Broadens beyond Claude while keeping the setup promise concrete.

## Page title and lede - settings mode

Original:

> Yoink settings.
>
> Manage local AI features and agent integration.

Proposed:

> Yoink settings.
>
> Manage local intelligence, API keys, and agent connections.

Rationale: Names the real jobs of this path for returning users.

## Page title and lede - offline recovery

Original:

> Yoink isn't running yet.
>
> Start the Yoink helper and this page will detect it automatically.

Proposed:

> Start the local Yoink helper.
>
> The extension is installed. Now start the local helper so Yoink can extract and save corpora on this computer.

Rationale: Explains the split between extension and helper without sounding like an error state.

## Step 1 heading

Original:

> What is Yoink?

Proposed:

> What Yoink gives your AI

Rationale: Frames the value as input quality, not just app identity.

## Step 1 body

Original:

> Yoink turns YouTube videos into structured input for Claude or ChatGPT - full transcript, screenshots at any interval, comments, and channel context, packaged into one paste-ready document.

Proposed:

> Yoink turns YouTube videos into structured AI-ready corpora: transcript, timestamps, screenshots, comments, channel context, metadata, and a clean markdown file you can paste into Claude, ChatGPT, Cursor, or any agent workflow.

Rationale: Keeps the feature list but updates the surface to include agents and saved corpora.

## Step 1 subcopy

Original:

> Skip the "summarize this for me" routine. Hand the model the whole video and ask it anything.

Proposed:

> Skip brittle summaries. Give the model the source material and ask better questions.

Rationale: Same point, cleaner and more durable.

Persona variant - creator:

> Give your AI the source material behind the video so it can help with hooks, structure, comments, and next-video ideas.

Persona variant - marketer/operator:

> Give your AI the source material behind the market so it can pull patterns, objections, hooks, and positioning angles.

## Step 2 heading

Original:

> Install the Yoink helper

Proposed:

> Install the local helper

Rationale: "Local helper" reinforces why a desktop installer exists.

## Step 2 body

Original:

> Yoink runs a tiny program on your computer that does the heavy lifting locally - that's why your data stays private. Nothing leaves your machine except the YouTube transcript request.

Proposed:

> Yoink runs a small helper on your computer. It downloads public YouTube data, builds the corpus locally, and saves everything to your Yoink folder. No Yoink cloud. No account.

Rationale: More explicit about local-first and less hand-wavy about privacy.

## Installer mock caption

Original:

> Double-click to install. Takes about 30 seconds.

Proposed:

> Double-click to install the helper, Start Menu shortcut, and auto-start entry.

Rationale: Sets expectations for what the installer changes on Windows.

## Step 3 heading

Original:

> Verify Yoink is running

Proposed:

> Verify the helper is running

Rationale: Distinguishes the helper from the extension.

## Step 3 body

Original:

> This page will detect Yoink the moment it comes online.

Proposed:

> Keep this page open. It checks your local helper and flips green as soon as Yoink is ready.

Rationale: Gives the user a clear action and explains the live status.

## Offline instructions

Original:

> Looks like Yoink isn't running yet. Start it from your Start Menu:

Proposed:

> Yoink is installed, but the helper is not running yet. Start it from your Start Menu:

Rationale: Reduces panic. The user learns what is missing.

## Step 4 heading

Original:

> You're all set! Try your first yoink.

Proposed:

> You're ready. Build your first corpus.

Rationale: Moves from novelty verb to durable product object.

## Step 4 body

Original:

> Here's a fresh one to test with - pick this one or any YouTube video you've been meaning to dig into.

Proposed:

> Try the suggested video, or open any YouTube video you want to research. Yoink will copy the structured corpus when it finishes.

Rationale: Sets expectation for the output and keeps choice open.

## Step 4 subcopy

Original:

> Or click the orange Yoink button under any YouTube video.

Proposed:

> After this, use the orange Yoink button under any YouTube video, playlist, or supported popup flow.

Rationale: Reflects v2 surface area more accurately.

## Comment Intelligence heading

Original:

> Comment Intelligence

Proposed:

> Comment Intelligence

Rationale: Keep. It is clear and product-specific.

## Comment Intelligence body

Original:

> Optionally analyze the top YouTube comments with your own Anthropic API key. Normal Yoink works without this.

Proposed:

> Optional: use your Anthropic API key to cluster the top comments into themes, mentioned products, and real disagreements. Normal Yoink works without a key.

Rationale: Says what the feature actually produces.

## API key storage note

Original:

> Your key is stored locally on your machine. Treat it like any other credential - protect your system.

Proposed:

> Your key is stored locally in your operating system credential store. Yoink never returns it to the extension or logs it.

Rationale: Updates copy for Sprint 7 keyring behavior and builds trust.

## Hook Type section

Original:

> Classify each video's opening hook style with the same Anthropic API key.

Proposed:

> Classify each video's opening hook - curiosity gap, question, contrarian, demo, stakes, and more - using the same Anthropic API key.

Rationale: Gives the user a reason to care without overexplaining taxonomy.

Persona variant - creator:

> Build a local library of hook patterns from the videos you study.

Persona variant - marketer/operator:

> Build a local signal library for which messages open attention in your market.

## Smart Screenshot Picker section

Original:

> When enabled, the popup shows a thumbnail grid after extraction so you can choose which screenshots get included in the clipboard paste. Single-video yoinks only - playlists are always text-only in clipboard.

Proposed:

> When enabled, single-video yoinks show a screenshot grid before paste so you choose the frames your AI sees. Playlists stay text-only in the clipboard to keep context manageable.

Rationale: Shorter and clearer about why playlists differ.

## MCP heading

Original:

> Agent Integration

Proposed:

> Agent Integration

Rationale: Keep. It is understandable for returning users coming from popup deep links.

Optional stronger heading:

> Use Yoink from your AI agent

Rationale: Better for first-time MCP users, less abstract than "Agent Integration."

## MCP body

Original:

> MCP lets your AI agent - Claude, ChatGPT, Cursor, and others - yoink YouTube content as a local tool, the same way it calls any other tool.

Proposed:

> MCP lets Claude Desktop, Cursor, and other AI agents call Yoink as a local tool: yoink a video, search saved corpora, inspect a transcript, or run comment and hook analysis.

Rationale: Names the actual tool jobs and avoids implying every client is officially tested.

## MCP subcopy

Original:

> Yoink does not edit your agent configs automatically. Copy the snippet for your client and paste it into that client's MCP settings.

Proposed:

> Yoink does not edit app config files automatically. Copy the snippet for your client and paste it into that client's MCP settings. Claude Desktop and Cursor are the launch-tested paths.

Rationale: Adds compatibility honesty from `docs/v2-mcp.md`.

## Stdio entry point label

Original:

> Stdio entry point

Proposed:

> Local command for agent clients

Rationale: Less protocol jargon while still accurate.

## HTTP MCP label

Original:

> HTTP MCP

Proposed:

> Local HTTP endpoint

Rationale: Avoids overclaiming generic HTTP MCP compatibility until the transport story is fully tested.

## Footer

Original:

> Made by ReplayRyan - ryanbiddy.com/yoink

Proposed:

> Built by ReplayRyan. Local-first, open source, and made for people who use AI for real research.

Rationale: Uses the footer to reinforce positioning, not just authorship.
