# AI Cloze Splitter (Anki Add-on)

This add-on adds a **Card Browser** action that uses an AI model to refactor large cloze notes into multiple atomic cloze cards.

## Features

- Adds **Browser → Split cloze into atomic cards (AI)**.
- Calls an OpenAI-compatible API to split a selected note into multiple cloze field values.
- Copies original fields/tags and adds optional tags to new notes.
- Optional AnkiConnect output path for users who prefer to route note creation through AnkiConnect.

## Setup

1. Install the add-on by copying this folder into your Anki `addons21` directory.
2. Open Anki → Tools → Add-ons → AI Cloze Splitter → Config.
3. Fill in the `api_key` and optional overrides.

### Config options

- `api_key`: OpenAI-compatible API key.
- `api_base`: Base URL for the API (default: `https://api.openai.com/v1`).
- `model`: Model name to call.
- `target_field_name`: If set, the add-on will use this field as the source text; otherwise it uses the first field.
- `add_tags`: Tags added to new notes.
- `max_splits_per_note`: Upper limit on splits returned per note.
- `request_timeout_s`: Timeout for API and AnkiConnect calls.
- `use_ankiconnect`: If `true`, add-on will use AnkiConnect to create notes.
- `ankiconnect_url`: URL for the AnkiConnect server (default: `http://127.0.0.1:8765`).

## Usage

1. Open the **Browser**.
2. Select one or more notes.
3. Click **Split cloze into atomic cards (AI)**.
4. The add-on will create new notes with one cloze deletion per note.

## Compatibility

- Manifest `min_anki_version` is set to `2.1.50`.
- Network access is required for the AI request and (optionally) AnkiConnect.

## Notes on AnkiConnect

When `use_ankiconnect` is enabled, the add-on sends `addNote` actions to AnkiConnect to create notes. This is optional; leaving it off keeps note creation entirely within Anki's native APIs.
