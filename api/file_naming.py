"""Naming-token engine for Milestone 2's own import/rename step
(2026-09-17) -- re-implements the *spec* of Whisparr's own
`{Token}`-based naming (`src/NzbDrone.Core/Organizer/FileNameBuilder.cs`,
GPL-3.0/C#), not its code (different language/runtime, verified live
this session there's no way to reuse the actual implementation). The
token vocabulary below mirrors Whisparr's own scene-naming tokens
(`{Scene Title}`, `{Studio Title}`, `{Release Date}`, `{Quality Full}`,
and Whisparr's own `{Episode PerformersFemale/Male/Other}` split,
renamed `{Performers Female/Male/Other}` here) so the user's existing
format strings (seeded as this project's own default Settings values,
see routers/settings.py's DEFAULTS) produce the same filenames as
before the cutover.

Deliberately simpler than Whisparr's own FileNameBuilder.cs in two
ways, both fine for this project's actual deployment:
- No Windows-reserved-device-name handling (CON/PRN/AUX/...) -- this
  sidecar and the Stash instance it writes into are Linux-only.
- No optional per-token prefix/suffix stripping when a token is empty
  (Whisparr's `{-token}`/`{token-}` bracket-stripping syntax) -- every
  token this project's own scenes actually have is always populated
  from StashDB, so an empty-token edge case doesn't arise in practice.
"""
import json
import re

ILLEGAL_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*]')

TOKEN_PATTERN = re.compile(r"\{([A-Za-z ]+)\}")
OPTIONAL_PATTERN = re.compile(r"<([^<>]*)>")


def sanitize(value):
    """Replaces filesystem-illegal characters with a space, collapsing
    repeats -- mirrors Whisparr's "Replace Illegal Characters" mode
    (the default; this project doesn't yet expose the alternate
    "remove instead" toggle, see PROGRESS.md's MVP-scope note)."""
    cleaned = ILLEGAL_CHARS_PATTERN.sub(" ", value)
    return re.sub(r"\s+", " ", cleaned).strip()


def build_tokens(scene_detail, quality_full, tags=None, options=None):
    """`scene_detail` is stashdb_client.get_scene_detail()'s own return
    shape ({"title", "date", "studio": {"name"}, "performers": [{"performer": {"name", "gender"}}]}).
    Returns the flat {token_name: value} dict render() substitutes --
    matching Whisparr's own `{Episode PerformersFemale/Male/Other}`
    split (this project can actually populate it more reliably than
    Whisparr ever could, thanks to direct StashDB access instead of
    whatever metadata happened to come back with the grabbed release)."""
    performers = scene_detail.get("performers") or []
    by_gender = {"FEMALE": [], "MALE": [], "OTHER": []}
    for p in performers:
        performer = p.get("performer") or {}
        gender = (performer.get("gender") or "OTHER").upper()
        name = performer.get("name")
        if name:
            by_gender.setdefault(gender, by_gender["OTHER"]).append(name)

    studio = scene_detail.get("studio") or {}
    options = options or {}
    date = scene_detail.get("date") or ""
    source, _, resolution = (quality_full or "").partition("-")
    tokens = {
        "Scene Title": scene_detail.get("title") or "",
        "Studio Title": studio.get("name") or "",
        "Release Date": date,
        "Release Year": date[:4] if re.match(r"^\d{4}", date) else "",
        "Quality Full": quality_full or "",
        "Source": source if resolution else "",
        "Resolution": resolution,
        "Performers Female": ", ".join(by_gender["FEMALE"]),
        "Performers Male": ", ".join(by_gender["MALE"]),
        "Performers Other": ", ".join(by_gender["OTHER"]),
        "Tag Folder": tag_folder_for(tags, options.get("tag_folders")),
    }
    return apply_replacements(tokens, options.get("replacements"))


# ---------- modular naming (v0.193.0): tag folders, word replacement, settings ----------

REPLACE_MODES = ("text", "word", "regex")
TEXT_TOKENS = ("Scene Title", "Movie Title", "Studio Title", "Performers Female", "Performers Male", "Performers Other", "Tag Folder")


def tag_folder_for(tags, tag_folders):
    """The folder name of the FIRST rule (in the list's order) whose tag the scene has -- the order is the priority, so several matching tags never
    compete; tags that are not in the list are ignored. "" when no rule matches. `tags`: names (any case); rule: {"tag", "folder" (default: the tag)}."""
    have = {str(t).strip().lower() for t in (tags or []) if t}
    for rule in tag_folders or []:
        tag = str(rule.get("tag") or "").strip()
        if tag and tag.lower() in have:
            return str(rule.get("folder") or tag).strip()
    return ""


def apply_replacements(tokens, replacements):
    """Word replacement on the text tokens (title, studio, performers, tag folder), rules in order: {"find", "replace", "mode"}:
    'text' = any occurrence, case-insensitive; 'word' = whole word only; 'regex' = a regular expression. A rule that does not compile is skipped."""
    rules = [r for r in (replacements or []) if str(r.get("find") or "")]
    if not rules:
        return tokens
    out = dict(tokens)
    for name in TEXT_TOKENS:
        value = out.get(name) or ""
        for r in rules:
            find, repl, mode = str(r["find"]), str(r.get("replace") or ""), r.get("mode") or "text"
            try:
                if mode == "regex":
                    value = re.sub(find, repl, value)
                elif mode == "word":
                    value = re.sub(r"(?<![\w])" + re.escape(find) + r"(?![\w])", lambda m, s=repl: s, value, flags=re.I)
                else:
                    value = re.sub(re.escape(find), lambda m, s=repl: s, value, flags=re.I)
            except re.error:
                continue
        out[name] = value
    return out


def parse_json_list(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def naming_options(setting):
    """{"replacements", "tag_folders"} from the sidecar settings (`setting(key)` returns the stored string)."""
    return {"replacements": parse_json_list(setting("name_replacements")), "tag_folders": parse_json_list(setting("tag_folders"))}


def render(format_string, tokens):
    """Substitutes every `{Token}` in `format_string` with its value
    from `tokens` (empty string if the token name isn't recognized --
    same "just drop it" behavior as an unrecognized token would
    effectively have, rather than raising over a typo'd Settings-page
    format string), then sanitizes the WHOLE result once at the end
    (matches Whisparr's own approach: illegal characters can appear
    inside a token's own value, e.g. a scene title containing a colon,
    not just in the literal format string)."""
    def substitute(match):
        return tokens.get(match.group(1), "")

    def optional(match):
        # <...>: an optional part (v0.193.0). Kept (without the < >) only when EVERY token inside has a value, else it vanishes whole,
        # separators included: "{Scene Title}< - {Release Date}>" -> no " - " for a scene without a date. < > are illegal in file names anyway,
        # so no existing format contains them literally.
        inner = match.group(1)
        names = TOKEN_PATTERN.findall(inner)
        return inner if all(tokens.get(n) for n in names) else ""

    raw = TOKEN_PATTERN.sub(substitute, OPTIONAL_PATTERN.sub(optional, format_string))
    return sanitize(raw)


# A file or folder name may hold at most 255 BYTES on Linux file systems (ext4, btrfs, NFS, SMB); titles with curly quotes or non-Latin letters take
# more than one byte per character. Whisparr/Radarr shorten for the same reason. 240 leaves room for a " (1)" style suffix.
MAX_NAME_BYTES = 240


def _bytes(text):
    return len(text.encode("utf-8"))


def _cut(text, max_bytes):
    """`text` cut to at most max_bytes UTF-8 bytes, never inside a character, at a word boundary where there is one."""
    if _bytes(text) <= max_bytes:
        return text
    cut = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    if " " in cut and len(cut) > max_bytes // 2:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" .-,")


def render_fitting(format_string, tokens, extension="", title_token="Scene Title"):
    """render() whose result (plus the extension) fits MAX_NAME_BYTES: when it is too long the scene title is shortened ("..." at the end) and only
    that -- date, quality, studio stay whole; without a title token in the format the whole name is cut. Names that fit are unchanged."""
    name = render(format_string, tokens)
    room = MAX_NAME_BYTES - _bytes(extension)
    if _bytes(name) <= room:
        return name
    title = tokens.get(title_token) or ""
    if title and "{" + title_token + "}" in format_string:
        excess = _bytes(name) - room + 3
        shortened = _cut(title, max(8, _bytes(title) - excess)) + "..."
        name = render(format_string, {**tokens, title_token: shortened})
    return _cut(name, room) if _bytes(name) > room else name


def render_path(folder_format, filename_format, tokens, extension, title_token="Scene Title"):
    """Folder and filename are sanitized independently (a `/` inside a
    single token's value must never accidentally create an extra path
    segment) then joined -- `folder_format` may itself contain literal
    `/` separators (e.g. `scenes/{Studio Title}/...`), which render()
    would otherwise sanitize away since `/` is on the illegal-character
    list. Each folder_format SEGMENT is rendered/sanitized on its own,
    the literal `/` separators are preserved."""
    folder_segments = [render_fitting(segment, tokens, title_token=title_token) for segment in folder_format.split("/")]
    folder = "/".join(s for s in folder_segments if s)
    filename = render_fitting(filename_format, tokens, extension, title_token) + extension
    return f"{folder}/{filename}" if folder else filename
