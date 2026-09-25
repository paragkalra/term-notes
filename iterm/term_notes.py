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
import collections
import contextlib
import datetime
import glob
import hashlib
import logging
import os
import re
import stat
import tempfile
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
    config["file_name"] = check_file_name(config["file_name"])
    return config


PLACEHOLDERS = ("title", "dir", "id")


PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def check_file_name(template):
    """Validate a file_name template; appends {id} if it is missing.

    Only plain {title}, {dir} and {id} are allowed -- no format specs,
    conversions or {{escapes}} -- so the WezTerm plugin can apply exactly the
    same rules. Glob characters are rejected because the template is also
    used as a glob pattern to find the tab's file after a title change.
    """
    unknown = sorted({"{" + name + "}" for name in PLACEHOLDER.findall(template)
                      if name not in PLACEHOLDERS})
    if unknown:
        raise ValueError(
            f"config: file_name {template!r} has unknown placeholder {', '.join(unknown)}; "
            "use {title}, {dir} and {id}")
    literal = PLACEHOLDER.sub("", template)
    if re.search(r"[{}]", literal):
        raise ValueError(f"config: file_name {template!r} has an unmatched {{ or }}")
    if re.search(r"[*?\[\]]", literal):
        raise ValueError(f"config: file_name {template!r} can't contain * ? [ or ]")
    if re.search(r"[/\\]", literal):
        # Keeps every notes file directly inside notes_dir (no "../").
        raise ValueError(f"config: file_name {template!r} can't contain / or \\")
    if "{id}" not in template:
        template += "_{id}"
    return template


def render_name(template, fields):
    return PLACEHOLDER.sub(lambda m: fields[m.group(1)], template)


def slug(text):
    # Keep letters/digits (any language), turn everything else -- including
    # the spinner glyphs agents like Claude Code put in titles -- into dashes.
    return re.sub(r"[^\w.]+", "-", text or "").strip("-.")[:80] or "tab"


def short_id(session_id):
    # 64 bits of the session UUID: collisions stay negligible even across
    # years of accumulated notes files (32 bits would not).
    return session_id.replace("-", "").lower()[:16]


def notes_file_for(notes_dir, template, session_id, title, cwd):
    """Return the tab's notes file, renaming it if the tab title changed.

    The {id} in the file name identifies the tab's file across title changes
    and script restarts. (Split panes are separate sessions, so each pane
    gets its own file.)
    """
    fields = {"title": slug(title), "dir": slug(os.path.basename(cwd or "")),
              "id": short_id(session_id)}
    wanted = os.path.join(notes_dir, render_name(template, fields) + ".md")
    existing = find_notes_files(notes_dir, template, fields["id"])
    if not existing:
        # Files from earlier versions end in an 8-character id; migrate one
        # if found. (This glob can't match new 16-character ids.)
        existing = find_notes_files(notes_dir, template, fields["id"][:8])
    if not existing or wanted in existing:
        return wanted
    if len(existing) > 1:
        # Ambiguous (e.g. after a template change): don't rename anything,
        # keep writing to the most recently used file.
        newest = max(existing, key=os.path.getmtime)
        log.warning("several notes files match this tab; using %s", newest)
        return newest
    return rename_no_clobber(existing[0], wanted)


def find_notes_files(notes_dir, template, file_id):
    wildcard = render_name(template, {"title": "*", "dir": "*", "id": file_id}) + ".md"
    return glob.glob(os.path.join(glob.escape(notes_dir), wildcard))


def rename_no_clobber(src, dst):
    """Rename src to dst unless dst exists; returns the path now in use."""
    try:
        os.link(src, dst)  # fails if dst exists, unlike os.rename
    except OSError as e:
        # dst exists, or the filesystem has no hard links. There is no safe
        # rename without them (check-then-rename races), so keep the old name.
        log.warning("not renaming %s to %s: %s", src, dst, e)
        return src
    os.remove(src)
    return dst


def clean_lines(text):
    """Tidy a terminal selection without changing its structure.

    Trims trailing padding, drops blank lines only at the start and end, and
    removes the indentation shared by every line. Blank lines and relative
    indentation inside the selection (code, stack traces, tables) are kept.
    """
    lines = [line.rstrip() for line in (text or "").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    # The whitespace prefix every line shares, character for character, so
    # mixed tabs and spaces are never cut into.
    indent = os.path.commonprefix(
        [line[:len(line) - len(line.lstrip())] for line in lines if line])
    return [line[len(indent):] for line in lines]


def abbreviate_home(path):
    home = os.path.expanduser("~")
    if path and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def one_line(text):
    # Metadata must stay on one line: a newline followed by "## " would look
    # like the start of another note to remove_last_note.
    return re.sub(r"\s*[\r\n]+\s*", " ", text).strip() if text else text


def format_note(lines, *, when, title=None, cwd=None, git=None, comment=None):
    """Render one note. The format is shared with the WezTerm plugin."""
    title, cwd, comment = one_line(title), one_line(cwd), one_line(comment)
    header = f"## {when:%Y-%m-%d %H:%M}"
    if title:
        header += f" · {title}"
    context = []
    if cwd:
        context.append(f"`{abbreviate_home(cwd)}`")
    if git:
        repo, branch = map(one_line, git)
        context.append(f"`{repo}` @ `{branch}`")
    parts = [header]
    if context:
        parts.append(" · ".join(context))
    # Blank lines become ">" so the quote stays one block.
    parts.append("\n".join(f"> {line}".rstrip() for line in lines))
    if comment:
        parts.append(f"**Comment:** {comment.strip()}")
    return "\n\n".join(parts) + "\n\n"


def append_note(notes_file, note):
    """Append a note; on failure the file is cut back to its original size,
    so a disk-full or interrupted write never leaves half a note behind."""
    os.makedirs(os.path.dirname(notes_file), exist_ok=True)
    existed = os.path.exists(notes_file)
    with open(notes_file, "ab") as f:
        size = f.seek(0, os.SEEK_END)
        try:
            f.write(note.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                f.truncate(size)
                if not existed:  # don't leave an empty file behind
                    os.remove(notes_file)
            raise


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
        # Write a uniquely named temp file with the original's permissions and
        # rename it over the original, so a failed write can't truncate the
        # notes that are being kept.
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(notes_file) or ".",
            prefix="." + os.path.basename(notes_file) + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), stat.S_IMODE(os.stat(notes_file).st_mode))
                f.write(remaining)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, notes_file)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise
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
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        # Kill and reap a hung command so they can't pile up.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.communicate()
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


def digest(text):
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def load_bindings(load=None):
    """Read the shortcut bindings, collecting config problems instead of raising.

    Returns (bindings, errors). If the config can't be loaded at all, the
    default shortcuts are used so the tool keeps working; saves will then
    show the config error in an alert.
    """
    errors = []
    try:
        keys = (load or load_config)()["keys"]
    except Exception as e:
        errors.append(str(e) or repr(e))
        keys = DEFAULTS["keys"]
    by_key = collections.defaultdict(list)
    for name, spec in keys.items():
        if name not in NoteTaker.ACTIONS:
            errors.append(f"config: unknown key binding {name!r}")
        elif spec:
            try:
                by_key[parse_key(spec)].append((name, spec))
            except ValueError as e:
                errors.append(f"config: {e}")
    bindings = {}
    for key, uses in by_key.items():
        if len(uses) > 1:
            # Don't guess which one wins: disable all of them.
            errors.append("config: " + " and ".join(f"{n} ({s})" for n, s in uses)
                          + " use the same shortcut, so they are disabled")
        else:
            bindings[key] = uses[0]
    return bindings, errors


class NoteTaker:
    ACTIONS = ("save", "save_with_comment", "open_notes", "undo")

    def __init__(self, connection):
        self.connection = connection
        # session id -> digest of the last clipboard text saved, so a stale
        # clipboard isn't saved twice. (A digest, so closed sessions don't
        # keep whole selections in memory; see forget().)
        self.last_clipboard = {}
        # Shortcuts run as independent tasks. One lock per session makes saves
        # and undos in a tab run one at a time, so two quick presses can't
        # both pass the clipboard check before either has saved.
        self.locks = collections.defaultdict(asyncio.Lock)
        # session id -> actions running or waiting for its lock.
        self.active = collections.Counter()
        # Sessions that closed while they still had active actions.
        self.closed = set()

    def forget(self, session_id):
        """Drop per-session state once the session has closed.

        If actions are still running or queued for it, the last one to finish
        purges the state (they could otherwise re-add it afterwards).
        """
        if self.active[session_id]:
            self.closed.add(session_id)
        else:
            self.purge(session_id)

    def purge(self, session_id):
        self.last_clipboard.pop(session_id, None)
        self.locks.pop(session_id, None)
        self.active.pop(session_id, None)
        self.closed.discard(session_id)

    async def exclusive(self, session_id, action):
        """Run action while holding the session's lock."""
        # Counted before waiting for the lock, so queued actions count too.
        self.active[session_id] += 1
        try:
            async with self.locks[session_id]:
                await action()
        finally:
            self.active[session_id] -= 1
            if not self.active[session_id] and session_id in self.closed:
                self.purge(session_id)

    async def perform(self, name, session):
        """Run an action, showing an alert if it fails.

        The shortcut is swallowed by the keystroke filter, so without the
        alert a failed save would look exactly like a successful one.
        """
        action = {
            "save": lambda: self.save(session),
            "save_with_comment": lambda: self.save(session, ask_comment=True),
            "open_notes": lambda: self.open_notes(session),
            "undo": lambda: self.undo(session),
        }[name]
        try:
            await action()
        except Exception as e:
            log.exception("%s failed", name)
            await self.alert(session, f"term-notes: {name.replace('_', ' ')} failed", str(e) or repr(e))

    async def alert(self, session, title, message):
        await iterm2.Alert(
            title, message, session.window.window_id if session.window else None,
        ).async_run(self.connection)

    async def selected_text(self, session):
        """Return (text, from_clipboard)."""
        selection = await session.async_get_selection()
        if selection.subSelections:
            return await session.async_get_selection_text(selection), False
        # Apps with their own mouse handling (Claude Code, Codex, ...) make
        # the selection themselves and copy it to the clipboard, so iTerm2
        # never sees it. Fall back to the clipboard.
        text = await run("/usr/bin/pbpaste")
        if not text or digest(text) == self.last_clipboard.get(session.session_id):
            return None, False
        return text, True

    async def tab_title(self, session):
        title = await session.tab.async_get_variable("title") if session.tab else None
        return title or await session.async_get_variable("name")

    async def notes_file(self, session, config):
        title = await self.tab_title(session)
        cwd = await session.async_get_variable("path")
        return notes_file_for(config["notes_dir"], config["file_name"],
                              session.session_id, title, cwd)

    async def save(self, session, ask_comment=False):
        await self.exclusive(session.session_id, lambda: self._save(session, ask_comment))

    async def _save(self, session, ask_comment):
        config = load_config()
        text, from_clipboard = await self.selected_text(session)
        lines = clean_lines(text)
        if not lines:
            log.info("nothing selected")
            return
        comment = None
        if ask_comment:
            preview = lines[0] if len(lines[0]) <= 60 else lines[0][:57] + "..."
            comment = await iterm2.TextInputAlert(
                "Save note", f"“{preview}”", "Your comment", "",
                session.window.window_id if session.window else None,
            ).async_run(self.connection)
            if comment is None:  # cancelled
                return

        title = await self.tab_title(session)
        cwd = await session.async_get_variable("path")
        git = await git_context(cwd) if config["git_context"] else None
        notes_file = notes_file_for(config["notes_dir"], config["file_name"],
                                    session.session_id, title, cwd)
        append_note(notes_file, format_note(
            lines, when=datetime.datetime.now(), title=title, cwd=cwd, git=git,
            comment=comment))
        # Only now that the note is on disk, so a failed save can be retried.
        if from_clipboard:
            self.last_clipboard[session.session_id] = digest(text)
        log.info("saved %d line(s) to %s", len(lines), notes_file)

    async def undo(self, session):
        await self.exclusive(session.session_id, lambda: self._undo(session))

    async def _undo(self, session):
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
            await self.alert(session, "No notes yet", "Select some text in this tab and save it first.")
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
    actions = NoteTaker.ACTIONS
    running = set()  # background tasks; asyncio only keeps weak references

    async def dispatch(name, session_id):
        session = app.get_session_by_id(session_id)
        if session is not None:
            await notes.perform(name, session)

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

    bindings, errors = load_bindings()
    if errors:
        for error in errors:
            log.error("%s", error)
        # Not awaited: the alert waits for a click, and shortcuts should work
        # meanwhile.
        alert = iterm2.Alert("term-notes: problem with your config", "\n".join(errors))
        running.add(asyncio.create_task(alert.async_run(connection)))

    async def forget_closed_sessions():
        async with iterm2.SessionTerminationMonitor(connection) as monitor:
            while True:
                notes.forget(await monitor.async_get())

    running.add(asyncio.create_task(forget_closed_sessions()))

    async def on_keystroke(_connection, notification):
        modifiers = frozenset(map(iterm2.Modifier, notification.modifiers)) & SIGNIFICANT_MODIFIERS
        name, _ = bindings.get((modifiers, notification.keyCode), (None, None))
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
                 ", ".join(f"{name}={spec}" for name, spec in bindings.values()))
        await asyncio.Future()  # run until iTerm2 stops the script


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    iterm2.run_forever(main)
