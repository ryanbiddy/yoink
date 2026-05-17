# Yoink Privacy Policy

**Version 2.0 — effective May 16, 2026**

This policy explains exactly what Yoink does and does not do with your
data. It is written to be read, not to be skimmed past. If anything here
is unclear, email us (see *Contact* below).

## The short version

Yoink does not collect anything about you. There is no Yoink account, no
Yoink server in the cloud, no analytics, and no telemetry. Yoink runs on
your own machine, talks to YouTube to do its job, and — only if you turn
on the optional AI features — talks to Anthropic using an API key you
provide. That's the whole story. The rest of this document is detail.

## 1. What Yoink collects about you

Nothing.

Yoink has no user accounts, no sign-in, and no usage tracking. It does
not phone home. The local helper writes a diagnostic log to
`%LOCALAPPDATA%\Yoink\server.log` on your own computer to help you
troubleshoot; that log never leaves your machine and is never sent
anywhere.

## 2. What leaves your computer, and when

Two things, and only two:

- **YouTube requests — always.** To extract a video's transcript,
  screenshots, comments, and metadata, Yoink downloads that public data
  from YouTube. This is the one network call Yoink cannot do without. It
  happens every time you yoink a video.
- **Anthropic API requests — only if you opt in.** If you enable
  *Comment Intelligence* or *Hook Type classification* **and** provide
  your own Anthropic API key, Yoink sends the relevant video text
  (comments or transcript opening) to Anthropic's API to be analyzed.
  These features are off by default. If you never turn them on, Yoink
  never contacts Anthropic.

Nothing else leaves your machine. No analytics. No telemetry. No usage
tracking. No crash reporting. The extracted corpus, screenshots, and
files are written only to folders on your own computer.

## 3. Your Anthropic API key

If you use the optional AI features, you supply your own Anthropic API
key. Yoink stores that key in **Windows Credential Manager**, the
operating system's encrypted credential store. It is not kept in
plaintext, and it is not written into Yoink's settings file.

Your key is never transmitted anywhere except to Anthropic
(`api.anthropic.com`), in the authorization header of the API calls you
asked Yoink to make. Yoink itself never receives or stores your key on
any server — there is no Yoink server to receive it.

## 4. Your control over your data

- **Clear your API key at any time** from the Yoink setup page. If
  Anthropic rejects the key (for example, an expired key), Yoink also
  clears it automatically.
- **Disable any AI feature at any time** from the setup page. With them
  off, Yoink makes no Anthropic calls at all.
- **Uninstall Yoink at any time.** Remove the extension from chrome://extensions/ and uninstall the Yoink helper from Windows Settings → Apps; this also removes its auto-start entry. Files Yoink already saved to your Desktop remain yours to keep or delete.

## 5. Third parties

Yoink shares data with exactly one third party, and only when you
choose to use the optional AI features: **Anthropic**, the provider of
the Claude API. Their handling of that data is governed by Anthropic's
own privacy policy and the terms of your Anthropic account.

Yoink contains no data brokers, no analytics SDKs, no advertising
networks, and no third-party trackers of any kind.

(Separately, extracting a video necessarily contacts **YouTube** to
download its public data — the same as visiting the video in your
browser.)

## 6. Children

Yoink is a productivity tool for analyzing YouTube videos with AI. It is
not directed at children under 13. Yoink has no age gate because it
collects no personal information from anyone — there is nothing to
age-gate.

## 7. Changes to this policy

This policy carries a version number and effective date at the top. If
we make a material change to how Yoink handles data, we will update the
version, and the change will be communicated through an in-app notice or
noted in the release notes of the extension update that introduces it.

## 8. Contact

Questions, concerns, or corrections: **yoink@replayryan.com**

---

*Yoink is free and open source (MIT-licensed). You can read exactly what
it does in the source code at https://github.com/ryanbiddy/yoink.*
