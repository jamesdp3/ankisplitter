from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from anki.notes import Note
from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showInfo, showWarning

CLOZE_RE = re.compile(r"\{\{c\d+::")
CLOZE_REPLACER = "{{c1::"


@dataclass
class NotePayload:
    nid: int
    mid: Optional[int]
    model_name: Optional[str]
    deck_name: Optional[str]
    fields: List[str]
    fields_by_name: Dict[str, str]
    tags: List[str]
    target_field: str
    target_index: int


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


def _ankiconnect_request(action: str, params: Optional[dict], config: dict) -> dict:
    url = config.get("anki_connect_url", "http://127.0.0.1:8765")
    payload = {"action": action, "version": 6}
    if params:
        payload["params"] = params
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AnkiConnect error: {exc}") from exc
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["result"]


def _ankiconnect_available(config: dict) -> bool:
    try:
        _ankiconnect_request("version", None, config)
    except Exception:
        return False
    return True


def _ankiconnect_note_info(nid: int, config: dict) -> Tuple[dict, str]:
    info = _ankiconnect_request("notesInfo", {"notes": [nid]}, config)
    note = info[0]
    cards = _ankiconnect_request("findCards", {"query": f"nid:{nid}"}, config)
    deck_name = "Default"
    if cards:
        card_info = _ankiconnect_request("cardsInfo", {"cards": [cards[0]]}, config)
        deck_name = card_info[0]["deckName"]
    return note, deck_name


def _prepare_note_payloads(note_ids: Iterable[int], config: dict) -> List[NotePayload]:
    payloads: List[NotePayload] = []
    use_anki_connect = bool(config.get("use_anki_connect", True))
    ankiconnect_ok = False
    if use_anki_connect:
        ankiconnect_ok = _ankiconnect_available(config)
        if not ankiconnect_ok:
            showWarning("AnkiConnect is enabled in config but not available. Falling back to direct access.")

    for nid in note_ids:
        if ankiconnect_ok:
            note_info, deck_name = _ankiconnect_note_info(nid, config)
            fields_by_name = {name: details["value"] for name, details in note_info["fields"].items()}
            ordered_fields = sorted(
                note_info["fields"].items(), key=lambda item: item[1].get("order", 0)
            )
            fields = [value["value"] for _, value in ordered_fields]
            field_names = [name for name, _ in ordered_fields]
            preferred = config.get("target_field_name")
            target_field = preferred if preferred in fields_by_name else field_names[0]
            target_index = field_names.index(target_field)
            payloads.append(
                NotePayload(
                    nid=nid,
                    mid=None,
                    model_name=note_info["modelName"],
                    deck_name=deck_name,
                    fields=fields,
                    fields_by_name=fields_by_name,
                    tags=list(note_info.get("tags", [])),
                    target_field=target_field,
                    target_index=target_index,
                )
            )
        else:
            note = mw.col.getNote(nid)
            target_field = _target_field_name(note, config)
            target_index = note.keys().index(target_field)
            payloads.append(
                NotePayload(
                    nid=nid,
                    mid=note.mid,
                    model_name=None,
                    deck_name=None,
                    fields=list(note.fields),
                    fields_by_name={name: value for name, value in zip(note.keys(), note.fields)},
                    tags=list(note.tags),
                    target_field=target_field,
                    target_index=target_index,
                )
            )
    return payloads


def _create_split_notes_direct(payload: NotePayload, splits: List[str], config: dict) -> int:
    if not splits:
        return 0

    model = mw.col.models.get(payload.mid)
    added = 0
    max_splits = int(config.get("max_splits_per_note", 20))
    add_tags = config.get("add_tags", ["ai-split"])

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
        mw.col.addNote(new_note)
        added += 1
    return added


def _create_split_notes_ankiconnect(payload: NotePayload, splits: List[str], config: dict) -> int:
    if not splits:
        return 0

    added = 0
    max_splits = int(config.get("max_splits_per_note", 20))
    add_tags = config.get("add_tags", ["ai-split"])

    for raw in splits[:max_splits]:
        if not raw.strip():
            continue
        normalized = _normalize_cloze(raw)
        fields = dict(payload.fields_by_name)
        fields[payload.target_field] = normalized
        tags = list(payload.tags)
        for tag in add_tags:
            if tag not in tags:
                tags.append(tag)
        _ankiconnect_request(
            "addNote",
            {
                "note": {
                    "deckName": payload.deck_name or "Default",
                    "modelName": payload.model_name,
                    "fields": fields,
                    "tags": tags,
                }
            },
            config,
        )
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
                if config.get("use_anki_connect", True) and payload.model_name:
                    total_added += _create_split_notes_ankiconnect(payload, result.splits, config)
                else:
                    total_added += _create_split_notes_direct(payload, result.splits, config)
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
