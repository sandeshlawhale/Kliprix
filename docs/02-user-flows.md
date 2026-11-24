# 02 — User flows

## A. New generation (main flow)
1. User opens Home. Sees a link box ("Paste a video link") and an **Edit video style** button.
2. User pastes a link → app calls `POST /api/videos/inspect` → shows a card: thumbnail, title, channel, duration, and a badge "Chat replay found" or "No chat replay". Up to 3 cards; the box shows "3/3" when full.
3. User can remove a card, or change the style (button → `/style`, returns to Home with changes kept).
4. User clicks **Start**. A batch is created, the user is moved to `/history/[batchId]`.
5. Backend processes videos one after another, clips one at a time. The page updates live (SSE). Under each video a muted **checkpoint list** (Download video, Fetch chat replay, Analyze audio and motion, Find highlights, Transcribe, Pick clips, Edit clips) ticks off step by step, then disappears when the video is fully finished. Each clip card shows: Queued / Processing (with stage name and percent) / Done / Failed.
6. When a clip is Done the user can open it.

## B. History
- Sidebar lists past batches (newest first, grouped Today / Yesterday / Earlier). Each item shows the first video's title + "+N more" if multiple.
- Opening a batch shows videos grouped, each with its clips.
- User can delete a batch (dialog asks: delete files too? default yes).

## C. Edit and export a clip
1. Open clip → video player left, metadata + edit panel right.
2. User edits any of: trim start/end, on-video title, caption text (word fixes), caption style, background blur, crop mode, YouTube/LinkedIn text.
3. Text-only metadata edits save instantly. Edits that change the video mark the clip **"Edited — preview needed"**.
4. **Preview**: fast low-res render (540x960). Player switches to the preview.
5. **Export**: full-quality render to `data/exports/...`. Then **Download** saves that file through the browser.
6. Only one render runs at a time; renders wait in the same queue.

## D. Configure API
1. Open menu at bottom of sidebar → **Configure API**.
2. Choose provider (dropdown) → paste key → click **Load models** → choose model (dropdown, with "custom model id" option) → **Test** → **Save for this session** or **Save** (D-053: stored encrypted with the model and used again on every start). Models can be pinned to the top of the dropdown.
3. Key is kept in the browser session and in backend memory only. If the backend restarts, the frontend re-sends it automatically while the tab is open. Closing the tab forgets the key.
4. If no key is configured when the user clicks Start on Home, show a clear message with a button to Configure API. Do not start.

## E. Storage
Settings → Storage shows disk used by downloads, exports, previews. Setting "Source videos after processing": **Ask / Delete / Keep**. If total data exceeds the warning size (default 10 GB, configurable), show a warning banner with a cleanup button.

## F. Failure flows (must all be handled)
| Situation | Behavior |
|---|---|
| Invalid/unsupported URL | Inline error under the box, nothing is added |
| Video private, age-restricted, removed, or blocked | That video fails with a clear message; **other videos continue** |
| yt-dlp outdated | Message + "Update downloader" button in Settings |
| No chat replay | Continue with other signals, show "No chat replay" badge |
| No speech detected | Continue with audio + motion signals |
| LLM error / rate limit | Retry with backoff; if still failing, video fails with message "LLM error: ..." |
| No face found | Automatic full-frame layout, not an error |
| Backend restarts mid-job | On startup, running jobs are reset to queued and resumed from cached files |
| Disk full | Job fails with message, nothing is deleted automatically |
