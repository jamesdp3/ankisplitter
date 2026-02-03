# AI Cloze Splitter

An Anki add-on that splits large, complex cloze notes into smaller, atomic cloze cards using an AI model.
It adds a **Split cloze into atomic cards (AI)** action in the Card Browser. Select notes, run the action,
then the add-on will create new notes (one cloze per concept) based on the selected field.

## Requirements

- Anki 2.1.50+ (per `manifest.json`).
- An OpenAI-compatible API endpoint and model.
- [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (recommended/used by default).

If AnkiConnect is enabled in the configuration but not available, the add-on falls back to direct Anki
collection access and shows a warning.

## Configuration

Open the add-on config and set:

- `api_key`: Your API key.
- `api_base`: Base URL for the OpenAI-compatible endpoint.
- `model`: Model name.
- `use_anki_connect`: `true` to use AnkiConnect for note operations.
- `anki_connect_url`: URL for AnkiConnect (default `http://127.0.0.1:8765`).
- `target_field_name`: Optional field name to split; defaults to the first field.
- `add_tags`: Tags added to newly created notes.
- `max_splits_per_note`: Max new notes to create per source note.
- `request_timeout_s`: AI request timeout.

## Usage

1. Select one or more cloze notes in the Card Browser.
2. Use **Split cloze into atomic cards (AI)** from the browser menu.
3. The add-on calls the AI model, then creates new notes with single cloze deletions.

## Notes

- The add-on normalizes cloze syntax to `{{c1::...}}` for every new note.
- It preserves other fields and tags, and adds any tags configured in `add_tags`.
