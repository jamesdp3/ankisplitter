from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable, List, Optional

from anki.notes import Note
from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showInfo

CLOZE_RE = re.compile(r"\{\{c\d+::")
CLOZE_REPLACER = "{{c1::"


@dataclass
class NotePayload:
    nid: int
    mid: int
    model_name: str
    fields: List[str]
    tags: List[str]
    target_field: str
    target_index: int
    deck_name: str


@dataclass
class SplitResult:
    nid: int
    splits: List[str]
    warning: Optional[str] = None


def _get_config() -> dict:
    config = mw.addonManager.getConfig(__name__)
    if config is None:
        return {}
    return config


def _target_field_name(note: Note, config: dict) -> str:
    preferred = config.get("target_field_name")
    if preferred and preferred in note:
        return preferred
    return note.keys()[0]


def _normalize_cloze(text: str) -> str:
    return CLOZE_RE.sub(CLOZE_REPLACER, text)


def _extract_json_array(content: str) -> List[str]:
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        raise ValueError("AI response did not include a JSON array.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ValueError("AI response JSON was not an array.")
    return [str(item) for item in parsed]


def _build_prompt(text: str, max_splits: int) -> str:
    return (
        "You are helping split a large cloze note into atomic cloze cards. "
        "Given the text below, return a JSON array of strings. Each string should be a full "
        "field value that contains exactly ONE cloze deletion, covering a single concept. "
        "Preserve the original wording where possible, but simplify or prune unrelated "
        "sentences so each item is focused. Use cloze syntax like {{c1::answer}}. "
        f"Return at most {max_splits} items.\n\n"
        "TEXT:\n"
        f"{text}"
    )


def _call_ai(prompt: str, config: dict) -> List[str]:
    api_key = config.get("api_key")
    if not api_key:
        raise RuntimeError("Missing api_key in add-on config.")

    api_base = config.get("api_base", "https://api.openai.com/v1").rstrip("/")
    model = config.get("model", "gpt-4o-mini")
    timeout = int(config.get("request_timeout_s", 60))

    url = f"{api_base}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "Return only JSON."},
            {"role": "user", "content": prompt},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"API error: {exc.read().decode('utf-8')}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    content = result["choices"][0]["message"]["content"]
    return _extract_json_array(content)


def _prepare_note_payloads(note_ids: Iterable[int], config: dict) -> List[NotePayload]:
    payloads: List[NotePayload] = []
    for nid in note_ids:
        note = mw.col.getNote(nid)
        model = mw.col.models.get(note.mid)
        target_field = _target_field_name(note, config)
        target_index = note.keys().index(target_field)
        cards = note.cards()
        if cards:
            deck_name = mw.col.decks.name(cards[0].did)
        else:
            deck_name = mw.col.decks.name(mw.col.decks.current()["id"])
        payloads.append(
            NotePayload(
                nid=nid,
                mid=note.mid,
                model_name=model["name"],
                fields=list(note.fields),
                tags=list(note.tags),
                target_field=target_field,
                target_index=target_index,
                deck_name=deck_name,
            )
        )
    return payloads


def _ankiconnect_request(payload: dict, config: dict) -> dict:
    url = config.get("ankiconnect_url", "http://127.0.0.1:8765")
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    timeout = int(config.get("request_timeout_s", 60))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AnkiConnect error: {exc}") from exc
    if result.get("error"):
        raise RuntimeError(f"AnkiConnect error: {result['error']}")
    return result.get("result", {})


def _add_note_via_ankiconnect(payload: NotePayload, field_values: dict, tags: List[str], config: dict) -> None:
    _ankiconnect_request(
        {
            "action": "addNote",
            "version": 6,
            "params": {
                "note": {
                    "deckName": payload.deck_name,
                    "modelName": payload.model_name,
                    "fields": field_values,
                    "tags": tags,
                    "options": {"allowDuplicate": True},
                }
            },
        },
        config,
    )


def _create_split_notes(payload: NotePayload, splits: List[str], config: dict) -> int:
    if not splits:
        return 0

    model = mw.col.models.get(payload.mid)
    added = 0
    max_splits = int(config.get("max_splits_per_note", 20))
    add_tags = config.get("add_tags", ["ai-split"])
    use_ankiconnect = bool(config.get("use_ankiconnect", False))

    for raw in splits[:max_splits]:
        if not raw.strip():
            continue
        normalized = _normalize_cloze(raw)
        new_note = mw.col.newNote(model)
        for idx, field_name in enumerate(new_note.keys()):
            new_note[field_name] = payload.fields[idx]
        new_note[payload.target_field] = normalized
        new_note.tags = list(payload.tags)
        for tag in add_tags:
            if tag not in new_note.tags:
                new_note.tags.append(tag)
        if use_ankiconnect:
            field_values = {key: new_note[key] for key in new_note.keys()}
            _add_note_via_ankiconnect(payload, field_values, new_note.tags, config)
        else:
            mw.col.addNote(new_note)
        added += 1
    return added


def _process_note(payload: NotePayload, config: dict) -> SplitResult:
    text = payload.fields[payload.target_index]
    prompt = _build_prompt(text, int(config.get("max_splits_per_note", 20)))
    splits = _call_ai(prompt, config)
    return SplitResult(nid=payload.nid, splits=splits)


def _split_selected(browser) -> None:
    note_ids = browser.selectedNotes()
    if not note_ids:
        showInfo("No notes selected.")
        return

    config = _get_config()
    payloads = _prepare_note_payloads(note_ids, config)
    total_added = 0
    errors: List[str] = []

    def process_next(index: int) -> None:
        nonlocal total_added
        if index >= len(payloads):
            summary = f"Created {total_added} new notes."
            if errors:
                summary += "\n\nWarnings:\n" + "\n".join(errors)
            showInfo(summary)
            return

        payload = payloads[index]

        def task():
            return _process_note(payload, config)

        def on_done(result: SplitResult) -> None:
            nonlocal total_added
            try:
                total_added += _create_split_notes(payload, result.splits, config)
            except Exception as exc:  # pragma: no cover - defensive UI error handling
                errors.append(f"Note {payload.nid}: {exc}")
            process_next(index + 1)

        def on_fail(exc: Exception) -> None:
            errors.append(f"Note {payload.nid}: {exc}")
            process_next(index + 1)

        mw.taskman.run_in_background(task, on_done, on_fail)

    process_next(0)


def _add_browser_action(browser, menu):
    action = QAction("Split cloze into atomic cards (AI)", browser)
    action.triggered.connect(lambda: _split_selected(browser))
    menu.addAction(action)


def _init_browser_menu() -> None:
    gui_hooks.browser_menus.append(_add_browser_action)


_init_browser_menu()
