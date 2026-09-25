#!/usr/bin/env python3
"""term-notes for iTerm2: save selected terminal text as Markdown notes.

Select text in any tab and press a shortcut. The text is appended to a
Markdown file for that tab (named after the tab title) with a timestamp,
the working directory and git branch, and -- optionally -- your comment.

Install by placing (or symlinking) this file in
~/Library/Application Support/iTerm2/Scripts/AutoLaunch/ and enabling
Settings > General > Magic > Enable Python API. Configuration lives in
~/.config/term-notes/config.toml (see config.example.toml).

https://github.com/paragkalra/term-notes
"""
import asyncio
import datetime
import glob
import logging
import os
import re
import tomllib

import iterm2

__version__ = "0.1.0"

CONFIG_PATH = os.path.expanduser(
    os.environ.get("TERM_NOTES_CONFIG", "~/.config/term-notes/config.toml"))

DEFAULTS = {
    "notes_dir": "~/notes/term-notes",
    # Placeholders: {title}, {dir}, {id}. {id} is required so each tab keeps
    # its own file; it is appended automatically if missing.
    "file_name": "{title}_{id}",
    "git_context": True,
    # "split" opens the notes in a split pane, "app" in the default .md app.
    "open_in": "split",
    "split_command": "less -R +G",
    # Set a key to "" to disable it. Changes need a script restart.
    "keys": {
        "save": "ctrl+alt+h",
        "save_with_comment": "ctrl+alt+shift+h",
        "open_notes": "ctrl+alt+n",
        "undo": "ctrl+alt+z",
    },
}

log = logging.getLogger("term-notes")


# ---------------------------------------------------------------------------
# Pure helpers (unit tested; no iTerm2 connection needed)
# ---------------------------------------------------------------------------

def load_config(path=CONFIG_PATH):
    config = {**DEFAULTS, "keys": dict(DEFAULTS["keys"])}
    if os.path.exists(path):
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for key, value in user.items():
            if key not in DEFAULTS:
                log.warning("config: ignoring unknown setting %r", key)
            elif key == "keys":
                config["keys"].update(value)
            else:
                config[key] = value
    config["notes_dir"] = os.path.expanduser(config["notes_dir"])
    if "{id}" not in config["file_name"]:
        config["file_name"] += "_{id}"
    return config


def slug(text):
    # Keep letters/digits (any language), turn everything else -- including
    # the spinner glyphs agents like Claude Code put in titles -- into dashes.
    return re.sub(r"[^\w.]+", "-", text or "").strip("-.")[:80] or "tab"


def short_id(session_id):
    return session_id.split("-")[0].lower()


def notes_file_for(notes_dir, template, session_id, title, cwd):
    """Return the tab's notes file, renaming it if the tab title changed.

    The {id} in the file name identifies the tab's file across title changes
    and script restarts. (Split panes are separate sessions, so each pane
    gets its own file.)
    """
    fields = {"title": slug(title), "dir": slug(os.path.basename(cwd or "")),
              "id": short_id(session_id)}
    wanted = os.path.join(notes_dir, template.format(**fields) + ".md")
    wildcard = template.format(title="*", dir="*", id=fields["id"]) + ".md"
    existing = glob.glob(os.path.join(glob.escape(notes_dir), wildcard))
    if existing and existing[0] != wanted:
        os.rename(existing[0], wanted)
    return wanted


def clean_lines(text):
    # Drop the trailing padding and blank lines terminal selections pick up.
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()]


def abbreviate_home(path):
    home = os.path.expanduser("~")
    if path and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def format_note(lines, *, when, title=None, cwd=None, git=None, comment=None):
    """Render one note. The format is shared with the WezTerm plugin."""
    header = f"## {when:%Y-%m-%d %H:%M}"
    if title:
        header += f" · {title}"
    context = []
    if cwd:
        context.append(f"`{abbreviate_home(cwd)}`")
    if git:
        repo, branch = git
        context.append(f"`{repo}` @ `{branch}`")
    parts = [header]
    if context:
        parts.append(" · ".join(context))
    parts.append("\n".join("> " + line.strip() for line in lines))
    if comment:
        parts.append(f"**Comment:** {comment.strip()}")
    return "\n\n".join(parts) + "\n\n"


def append_note(notes_file, note):
    os.makedirs(os.path.dirname(notes_file), exist_ok=True)
    with open(notes_file, "a", encoding="utf-8") as f:
        f.write(note)


def remove_last_note(notes_file):
    """Remove the last note from the file. Returns True if one was removed.

    Notes start with a "## " line; quoted text and comments never do.
    """
    if not os.path.exists(notes_file):
        return False
    with open(notes_file, encoding="utf-8") as f:
        content = f.read()
    starts = [m.start() for m in re.finditer(r"^## ", content, re.MULTILINE)]
    if not starts:
        return False
    remaining = content[:starts[-1]]
    if remaining.strip():
        with open(notes_file, "w", encoding="utf-8") as f:
            f.write(remaining)
    else:
        os.remove(notes_file)
    return True


def parse_key(spec):
    """Parse "ctrl+alt+shift+h" into (modifiers, keycode). Letters/digits only."""
    names = {
        "ctrl": iterm2.Modifier.CONTROL, "control": iterm2.Modifier.CONTROL,
        "alt": iterm2.Modifier.OPTION, "opt": iterm2.Modifier.OPTION,
        "option": iterm2.Modifier.OPTION, "shift": iterm2.Modifier.SHIFT,
        "cmd": iterm2.Modifier.COMMAND, "command": iterm2.Modifier.COMMAND,
    }
    *mods, key = spec.lower().replace(" ", "").split("+")
    if len(key) != 1 or not key.isalnum() or not key.isascii():
        raise ValueError(f"unsupported key {key!r} in {spec!r}: use a letter or digit")
    try:
        modifiers = frozenset(names[m] for m in mods)
    except KeyError as e:
        raise ValueError(f"unknown modifier {e.args[0]!r} in {spec!r}") from None
    return modifiers, iterm2.Keycode["ANSI_" + key.upper()].value


# ---------------------------------------------------------------------------
# iTerm2 integration
# ---------------------------------------------------------------------------

SIGNIFICANT_MODIFIERS = frozenset({
    iterm2.Modifier.CONTROL, iterm2.Modifier.OPTION,
    iterm2.Modifier.SHIFT, iterm2.Modifier.COMMAND,
})


async def run(*args, timeout=2):
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (OSError, TimeoutError):
        return None
    return out.decode("utf-8", errors="replace") if proc.returncode == 0 else None


async def git_context(cwd):
    if not cwd:
        return None
    toplevel = await run("git", "-C", cwd, "rev-parse", "--show-toplevel")
    if not toplevel:
        return None
    # `branch --show-current` also works before the first commit; it prints
    # nothing on a detached HEAD.
    branch = (await run("git", "-C", cwd, "branch", "--show-current") or "").strip()
    return os.path.basename(toplevel.strip()), branch or "detached"


class NoteTaker:
    def __init__(self, connection):
        self.connection = connection
        # session id -> last clipboard text saved, so a stale clipboard
        # isn't saved twice.
        self.last_clipboard = {}

    async def selected_text(self, session):
        selection = await session.async_get_selection()
        if selection.subSelections:
            return await session.async_get_selection_text(selection)
        # Apps with their own mouse handling (Claude Code, Codex, ...) make
        # the selection themselves and copy it to the clipboard, so iTerm2
        # never sees it. Fall back to the clipboard.
        text = await run("/usr/bin/pbpaste")
        if not text or text == self.last_clipboard.get(session.session_id):
            return None
        self.last_clipboard[session.session_id] = text
        return text

    async def tab_title(self, session):
        title = await session.tab.async_get_variable("title") if session.tab else None
        return title or await session.async_get_variable("name")

    async def notes_file(self, session, config):
        title = await self.tab_title(session)
        cwd = await session.async_get_variable("path")
        return notes_file_for(config["notes_dir"], config["file_name"],
                              session.session_id, title, cwd)

    async def save(self, session, ask_comment=False):
        config = load_config()
        lines = clean_lines(await self.selected_text(session))
        if not lines:
            log.info("nothing selected")
            return
        comment = None
        if ask_comment:
            preview = lines[0] if len(lines[0]) <= 60 else lines[0][:57] + "..."
            comment = await iterm2.TextInputAlert(
                "Save note",f"“{preview}”", "Your comment", "",
                session.window.window_id if session.window else None,
            ).async_run(self.connection)
            if comment is None:  # cancelled
                self.last_clipboard.pop(session.session_id, None)
                return

        title = await self.tab_title(session)
        cwd = await session.async_get_variable("path")
        git = await git_context(cwd) if config["git_context"] else None
        notes_file = notes_file_for(config["notes_dir"], config["file_name"],
                                    session.session_id, title, cwd)
        append_note(notes_file, format_note(
            lines, when=datetime.datetime.now(), title=title, cwd=cwd, git=git,
            comment=comment))
        log.info("saved %d line(s) to %s", len(lines), notes_file)

    async def undo(self, session):
        config = load_config()
        if not remove_last_note(await self.notes_file(session, config)):
            log.info("no notes to undo")
            return
        # Let the same clipboard text be saved again after undoing it.
        self.last_clipboard.pop(session.session_id, None)
        log.info("removed last note")

    async def open_notes(self, session):
        config = load_config()
        notes_file = await self.notes_file(session, config)
        if not os.path.exists(notes_file):
            await iterm2.Alert(
                "No notes yet", "Select some text in this tab and save it first.",
                session.window.window_id if session.window else None,
            ).async_run(self.connection)
            return
        if config["open_in"] == "app":
            await run("/usr/bin/open", notes_file)
            return
        pane = await session.async_split_pane(vertical=True)
        quoted = "'" + notes_file.replace("'", "'\\''") + "'"
        await pane.async_send_text(f"{config['split_command']} {quoted}; exit\n")


def keystroke_patterns(bindings):
    patterns = []
    for modifiers, keycode in bindings:
        pattern = iterm2.KeystrokePattern()
        pattern.required_modifiers = list(modifiers)
        pattern.forbidden_modifiers = list(SIGNIFICANT_MODIFIERS - modifiers)
        pattern.keycodes = [iterm2.Keycode(keycode)]
        patterns.append(pattern)
    return patterns


async def main(connection):
    app = await iterm2.async_get_app(connection)
    notes = NoteTaker(connection)
    actions = {
        "save": lambda s: notes.save(s),
        "save_with_comment": lambda s: notes.save(s, ask_comment=True),
        "open_notes": notes.open_notes,
        "undo": notes.undo,
    }

    async def dispatch(name, session_id):
        session = app.get_session_by_id(session_id)
        if session is None:
            return
        try:
            await actions[name](session)
        except Exception:
            log.exception("%s failed", name)

    # Script functions, for anyone who prefers binding keys in iTerm2 itself
    # (Settings > Keys > Key Bindings > Invoke Script Function), e.g.
    # term_notes_save(session_id: id)
    def make_rpc(name):
        async def rpc(session_id=iterm2.Reference("id")):
            await dispatch(name, session_id)
        rpc.__name__ = f"term_notes_{name}"
        return iterm2.RPC(rpc)

    for name in actions:
        await make_rpc(name).async_register(connection)

    bindings = {}
    for name, spec in load_config()["keys"].items():
        if name not in actions:
            log.warning("config: ignoring unknown key binding %r", name)
        elif spec:
            try:
                bindings[parse_key(spec)] = name
            except ValueError as e:
                log.error("config: %s", e)

    running = set()

    async def on_keystroke(_connection, notification):
        modifiers = frozenset(map(iterm2.Modifier, notification.modifiers)) & SIGNIFICANT_MODIFIERS
        name = bindings.get((modifiers, notification.keyCode))
        if name:
            # Run in a task so a comment prompt doesn't block other keys.
            # asyncio only keeps weak references to tasks, so hold on to it.
            task = asyncio.create_task(dispatch(name, notification.session))
            running.add(task)
            task.add_done_callback(running.discard)

    # The filter stops bound keys from also reaching the running program.
    async with iterm2.KeystrokeFilter(connection, keystroke_patterns(bindings)):
        await iterm2.notifications.async_subscribe_to_keystroke_notification(
            connection, on_keystroke)
        log.info("term-notes %s ready: %s", __version__,
                 ", ".join(f"{name}={load_config()['keys'][name]}" for name in bindings.values()))
        await asyncio.Future()  # run until iTerm2 stops the script


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    iterm2.run_forever(main)
