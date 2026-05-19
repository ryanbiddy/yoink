# Yoink Memory

Status: Sprint 18 / v2.0
Audience: Yoink users managing a growing local corpus.

## What It Is

Yoink Memory is the corpus gallery for everything you have yoinked.

It gives you one searchable, filterable view across your saved videos, instead of asking you to browse topic folders by hand. It is built on Yoink's local SQLite library index, so search stays fast as your library grows.

Yoink Memory is local-first:

- The page runs inside the Chrome extension.
- Search is served by the local helper on `127.0.0.1`.
- The index lives at `%LOCALAPPDATA%\Yoink\index.db`.
- Your corpora, screenshots, thumbnails, and sidecars stay on disk.
- There is no Yoink cloud service and no telemetry upload.

## How To Open It

Open the Yoink popup and click:

```text
View all yoinks ->
```

That opens the standalone Memory page in a new browser tab:

```text
chrome-extension://<extension-id>/yoink-memory.html
```

## Filters

Yoink Memory supports six filters.

| Filter | What it does |
|---|---|
| Search | Full-text search across indexed corpus text, title, channel, topic, hook type, and slug. |
| Channel | Exact channel-name filter. |
| Topic | Exact topic-folder filter. |
| Hook type | One of Yoink's 9 Hook Type categories, or all. |
| Date from | Inclusive lower bound on `yoinked_at`. |
| Date to | Inclusive upper bound on `yoinked_at`. |

Filters combine with AND logic. Each added filter narrows the result set.

The search box is debounced so typing does not fire a request on every keypress.

Filter state is stored in `chrome.storage.local`, so reopening the Memory page restores the last filters you used.

Use `Clear` to reset every filter and return to the full corpus.

## Each Row

Each result row is a compact summary of one yoink.

It includes:

- Thumbnail: `thumbnail.jpg` from the yoink folder, served through Yoink's token-gated `/file` endpoint.
- Title: video title from the sidecar/index.
- Metadata line: channel, topic, and yoink date.
- Hook chip: Hook Type classification when available.
- Confidence: classifier confidence when available, shown as `confidence X/5`.
- Entity count: number of extracted entities for that video.
- Health icons: compact status indicators for transcript, screenshots, comments, Hook Type, and Comment Intelligence.
- Action menu: row-specific actions.

The row hides unavailable signals instead of treating them as errors. Older yoinks, skipped AI features, or videos without entity extraction may show fewer badges.

## Actions

Open the three-dot action menu on a row to use row-specific actions.

### Open Folder

Opens the yoink's folder in Windows Explorer.

This uses the existing local helper `/open-folder` route. The helper only opens folders under the Yoink output root.

### Open On YouTube

Opens the original YouTube URL for the yoink when the index or sidecar has enough metadata to reconstruct it.

### Re-Yoink

Starts a fresh single-video extraction for the same YouTube URL.

Re-yoinking the same video updates the local index row keyed by `video_id`.

### Delete

Soft-deletes the yoink.

The row disappears from Memory immediately, but the folder is not hard-deleted right away. Yoink moves it into the local trash folder and keeps it restorable for 30 days.

## Soft Delete And 30-Day Trash

Deleting a yoink is intentionally low-friction. There is no confirm modal.

When you click Delete:

1. The helper marks the `yoinks.deleted_at` field in `index.db`.
2. The helper moves the folder into `_yoink-trash/`.
3. The Memory page hides the row because normal reads filter out deleted yoinks.
4. The page shows an undo-style toast.

Trash path format:

```text
<Yoink output root>\_yoink-trash\<topic>\<slug>__deleted-<timestamp>\
```

The timestamp is derived from the stored deletion time with characters removed that Windows cannot use in folder names.

Example:

```text
C:\Users\Ryan\Desktop\Yoink\_yoink-trash\AI and ML\the-new-code__deleted-2026-05-18T143022\
```

Deleted yoinks can be restored for 30 days through the restore API. The Memory page can call the same restore route when it exposes an undo or trash view.

After 30 days, the helper hard-deletes expired trash:

- The trash folder is removed from disk.
- The `yoinks` row is deleted from `index.db`.
- Related rows cascade out of `citations`, `entity_mentions`, and `taxonomy_corrections`.
- The standalone FTS row is removed by the index helper.

The purge runs once at helper startup and then every 24 hours while the helper is running.

## Restore Behavior

Restore reverses a soft delete while the trash folder still exists.

Restore:

1. Reads the deleted `yoinks` row by `video_id`.
2. Recomputes the trash folder path from `corpus_path` and `deleted_at`.
3. Moves the folder back to the original topic/slug location.
4. Clears `deleted_at`.
5. Makes the row visible in Memory again.

Restore can fail if:

- The trash folder is missing.
- The original folder path is already occupied.
- Windows blocks the move because a file is open or locked.

In those cases Yoink returns a friendly error and leaves the index state unchanged.

## Empty And Error States

Yoink Memory has three common empty/error states.

No yoinks yet:

```text
You haven't yoinked anything yet.
```

No matches for current filters:

```text
No yoinks match your filters.
```

Helper offline:

```text
Yoink helper is offline. Start it from the system tray.
```

The helper offline state means the extension page loaded, but `127.0.0.1:5179` did not answer authenticated requests.

## Performance Notes

Yoink Memory is backed by the Sprint 15 SQLite FTS5 index.

Important behavior:

- Search runs against `yoinks_fts`, not by scanning folders.
- Pagination defaults to 50 rows per page.
- `/memory/search` returns `total`, `limit`, `offset`, and only the visible page of results.
- Thumbnail files load on demand through `/file`.
- Browser image caching handles repeated thumbnail views.
- Soft-deleted rows are filtered at the database layer with `deleted_at IS NULL`.

The design target is fast search across thousands of local yoinks without requiring a cloud index.

## Privacy

Yoink Memory does not add a new network service.

The page talks only to the local helper server, and the helper reads local files and `index.db`. Optional AI features still follow the existing BYO Anthropic key rules, but browsing, filtering, soft delete, restore, thumbnails, and Memory search are local-only.
