import asyncio
import datetime
import os
import re
import subprocess

import iterm2
import pytest
from conftest import load_iterm_module

th = load_iterm_module()

SESSION = "934051CD-9B3F-4E91-AADF-EDB0803BADB9"
WHEN = datetime.datetime(2026, 9, 24, 15, 43)


@pytest.mark.parametrize("title, expected", [
    ("✳ iTerm highlight setup", "iTerm-highlight-setup"),
    ("⠐ Fix auth bug (PLAT-741)", "Fix-auth-bug-PLAT-741"),
    ("user@host:~", "user-host"),
    ("données été", "données-été"),
    ("", "tab"),
    (None, "tab"),
    ("✳", "tab"),
])
def test_slug(title, expected):
    assert th.slug(title) == expected


def test_slug_is_capped():
    assert len(th.slug("x" * 200)) == 80


def test_notes_file_created_from_title(tmp_path):
    path = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "✳ Claude Code", "/src/app")
    assert os.path.basename(path) == "Claude-Code_934051cd9b3f4e91.md"
    assert not os.path.exists(path)  # only named, created on first note


def test_notes_file_renamed_when_title_changes(tmp_path):
    first = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Claude Code", None)
    th.append_note(first, "## note\n\n")
    second = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Fix login bug", None)
    assert os.path.basename(second) == "Fix-login-bug_934051cd9b3f4e91.md"
    assert not os.path.exists(first)
    with open(second) as f:
        assert f.read() == "## note\n\n"


def test_notes_file_separate_per_tab(tmp_path):
    a = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Same", None)
    th.append_note(a, "## a\n\n")
    b = th.notes_file_for(str(tmp_path), "{title}_{id}", "AAAA1111-0000", "Same", None)
    assert a != b
    assert os.path.exists(a)


def test_notes_file_template_with_dir(tmp_path):
    path = th.notes_file_for(str(tmp_path), "{dir}-{title}_{id}", SESSION, "T", "/Users/me/my repo")
    assert os.path.basename(path) == "my-repo-T_934051cd9b3f4e91.md"


def test_notes_dir_with_glob_characters(tmp_path):
    notes_dir = tmp_path / "notes [work]"
    first = th.notes_file_for(str(notes_dir), "{title}_{id}", SESSION, "One", None)
    th.append_note(first, "## x\n\n")
    second = th.notes_file_for(str(notes_dir), "{title}_{id}", SESSION, "Two", None)
    assert os.path.exists(second) and not os.path.exists(first)


def test_clean_lines():
    text = "\n  \n  ⏺ The fix is in auth.ts   \n\n   \n  line two (see #42).   \n\n"
    # Outer blank lines and shared indent go; inner blank lines stay.
    assert th.clean_lines(text) == ["⏺ The fix is in auth.ts", "", "", "line two (see #42)."]
    assert th.clean_lines(None) == []
    assert th.clean_lines("\n  \n") == []


def test_clean_lines_keeps_code_structure():
    text = (
        "    def f(x):\n"
        "        if x:\n"
        "            return 1\n"
        "\n"
        "        return 2\n"
    )
    assert th.clean_lines(text) == [
        "def f(x):", "    if x:", "        return 1", "", "    return 2"]


def test_format_note_keeps_structure():
    lines = th.clean_lines("Traceback:\n  File a.py\n    boom()\n\nValueError: x")
    note = th.format_note(lines, when=WHEN)
    assert note == (
        "## 2026-09-24 15:43\n\n"
        "> Traceback:\n>   File a.py\n>     boom()\n>\n> ValueError: x\n\n"
    )


def test_format_note_full():
    home = os.path.expanduser("~")
    note = th.format_note(["first line", "second"], when=WHEN, title="✳ Claude Code",
                          cwd=home + "/src/app", git=("app", "main"), comment=" check this ")
    assert note == (
        "## 2026-09-24 15:43 · ✳ Claude Code\n\n"
        "`~/src/app` · `app` @ `main`\n\n"
        "> first line\n> second\n\n"
        "**Comment:** check this\n\n"
    )


def test_format_note_minimal():
    assert th.format_note(["hello"], when=WHEN) == "## 2026-09-24 15:43\n\n> hello\n\n"


def test_abbreviate_home():
    home = os.path.expanduser("~")
    assert th.abbreviate_home(home) == "~"
    assert th.abbreviate_home(home + "/x") == "~/x"
    assert th.abbreviate_home(home + "x/y") == home + "x/y"
    assert th.abbreviate_home("/tmp") == "/tmp"
    assert th.abbreviate_home(None) is None


def test_remove_last_note(tmp_path):
    path = str(tmp_path / "n.md")
    first = th.format_note(["one"], when=WHEN)
    second = th.format_note(["two", "## not a header"], when=WHEN, comment="## also not")
    th.append_note(path, first)
    th.append_note(path, second)
    assert th.remove_last_note(path)
    with open(path) as f:
        assert f.read() == first
    assert th.remove_last_note(path)
    assert not os.path.exists(path)  # empty file is removed
    assert not th.remove_last_note(path)


def test_load_config_defaults(tmp_path):
    config = th.load_config(str(tmp_path / "missing.toml"))
    assert config["notes_dir"] == os.path.expanduser("~/notes/term-notes")
    assert config["keys"]["save"] == "ctrl+alt+h"


def test_load_config_overrides(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        'notes_dir = "~/elsewhere"\n'
        'file_name = "{dir}-{title}"\n'
        'git_context = false\n'
        'bogus = 1\n'
        '[keys]\n'
        'undo = ""\n'
    )
    config = th.load_config(str(path))
    assert config["notes_dir"] == os.path.expanduser("~/elsewhere")
    assert config["file_name"] == "{dir}-{title}_{id}"
    assert config["git_context"] is False
    assert config["keys"]["undo"] == ""
    assert config["keys"]["save"] == "ctrl+alt+h"  # other keys keep defaults
    assert "bogus" in caplog.text
    # Defaults must not be mutated by a load.
    assert th.DEFAULTS["keys"]["undo"] == "ctrl+alt+z"


def test_example_config_is_valid():
    example = os.path.join(os.path.dirname(__file__), "..", "config.example.toml")
    config = th.load_config(example)
    for spec in config["keys"].values():
        if spec:
            th.parse_key(spec)


def test_parse_key():
    mods, keycode = th.parse_key("Ctrl + Alt + Shift + H")
    assert mods == {iterm2.Modifier.CONTROL, iterm2.Modifier.OPTION, iterm2.Modifier.SHIFT}
    assert keycode == iterm2.Keycode.ANSI_H.value
    assert th.parse_key("cmd+opt+7") == (
        frozenset({iterm2.Modifier.COMMAND, iterm2.Modifier.OPTION}), iterm2.Keycode.ANSI_7.value)


@pytest.mark.parametrize("spec", ["ctrl+alt+f5", "hyper+h", "ctrl+alt+é", "ctrl+"])
def test_parse_key_rejects(spec):
    with pytest.raises(ValueError):
        th.parse_key(spec)


def test_git_context(tmp_path):
    repo = tmp_path / "myrepo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
    assert asyncio.run(th.git_context(str(repo / "sub"))) == ("myrepo", "trunk")
    assert asyncio.run(th.git_context(str(tmp_path))) is None
    assert asyncio.run(th.git_context(None)) is None


def test_readme_documents_every_rpc_as_a_full_call():
    with open(os.path.join(os.path.dirname(__file__), "..", "README.md")) as f:
        readme = f.read()
    for action in th.DEFAULTS["keys"]:
        assert f"term_notes_{action}(session_id: id)" in readme


def test_keystroke_patterns_forbid_other_modifiers():
    [pattern] = th.keystroke_patterns([th.parse_key("ctrl+alt+h")])
    assert set(pattern.required_modifiers) == {iterm2.Modifier.CONTROL, iterm2.Modifier.OPTION}
    assert set(pattern.forbidden_modifiers) == {iterm2.Modifier.SHIFT, iterm2.Modifier.COMMAND}
    assert pattern.keycodes == [iterm2.Keycode.ANSI_H]


# --- Review fixes -----------------------------------------------------------

def test_undo_keeps_notes_when_rewrite_fails(tmp_path, monkeypatch):
    path = str(tmp_path / "n.md")
    original = th.format_note(["one"], when=WHEN) + th.format_note(["two"], when=WHEN)
    th.append_note(path, original)

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(th.os, "replace", failing_replace)
    with pytest.raises(OSError):
        th.remove_last_note(path)
    with open(path) as f:
        assert f.read() == original  # nothing lost
    assert os.listdir(tmp_path) == ["n.md"]  # temp file cleaned up


@pytest.mark.parametrize("field", ["title", "cwd", "comment"])
def test_multiline_metadata_cannot_fake_a_note_boundary(tmp_path, field):
    path = str(tmp_path / "n.md")
    first = th.format_note(["one"], when=WHEN)
    second = th.format_note(["two"], when=WHEN, **{field: "task\n## subtitle\r\nmore"})
    assert "task ## subtitle more" in second
    th.append_note(path, first)
    th.append_note(path, second)
    assert th.remove_last_note(path)
    with open(path) as f:
        assert f.read() == first


def test_run_kills_and_reaps_timed_out_process(monkeypatch):
    procs = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(th.asyncio, "create_subprocess_exec", recording_exec)
    assert asyncio.run(th.run("sleep", "30", timeout=0.2)) is None
    [proc] = procs
    assert proc.returncode is not None  # killed and reaped, not left running


def test_run_missing_command():
    assert asyncio.run(th.run("/nonexistent/command")) is None


class FakeSession:
    """Just enough of iterm2.Session for NoteTaker.save, with no selection."""

    session_id = SESSION
    window = None

    def __init__(self, cwd):
        self.cwd = cwd

        class Tab:
            async def async_get_variable(self, name):
                return "My tab"

        self.tab = Tab()

    async def async_get_selection(self):
        class Selection:
            subSelections = ()

        return Selection()

    async def async_get_variable(self, name):
        return {"path": self.cwd, "name": "zsh"}[name]


def test_clipboard_can_be_retried_after_failed_save(tmp_path, monkeypatch):
    async def fake_run(*args, timeout=2):
        return "copied by the app\n" if args[0] == "/usr/bin/pbpaste" else None

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    config = th.load_config(str(tmp_path / "missing.toml"))
    config["notes_dir"] = str(blocker / "notes")  # makedirs will fail
    monkeypatch.setattr(th, "run", fake_run)
    monkeypatch.setattr(th, "load_config", lambda: config)

    notes = th.NoteTaker(connection=None)
    session = FakeSession(str(tmp_path))
    with pytest.raises(OSError):
        asyncio.run(notes.save(session))

    config["notes_dir"] = str(tmp_path / "notes")
    asyncio.run(notes.save(session))  # retry works: not treated as already saved
    asyncio.run(notes.save(session))  # same clipboard again: skipped
    [name] = os.listdir(tmp_path / "notes")
    assert name == "My-tab_934051cd9b3f4e91.md"
    assert (tmp_path / "notes" / name).read_text().count("> copied by the app") == 1


def test_git_metadata_cannot_fake_a_note_boundary(tmp_path):
    path = str(tmp_path / "n.md")
    first = th.format_note(["one"], when=WHEN)
    second = th.format_note(["two"], when=WHEN, git=("my\n## repo", "feat\n## x"))
    assert "`my ## repo` @ `feat ## x`" in second
    th.append_note(path, first)
    th.append_note(path, second)
    assert th.remove_last_note(path)
    with open(path) as f:
        assert f.read() == first


def test_undo_preserves_permissions_and_existing_tmp(tmp_path):
    path = tmp_path / "n.md"
    th.append_note(str(path), th.format_note(["one"], when=WHEN))
    th.append_note(str(path), th.format_note(["two"], when=WHEN))
    path.chmod(0o600)
    stray = tmp_path / "n.md.tmp"
    stray.write_text("someone else's file")
    assert th.remove_last_note(str(path))
    assert path.stat().st_mode & 0o777 == 0o600
    assert stray.read_text() == "someone else's file"
    assert sorted(os.listdir(tmp_path)) == ["n.md", "n.md.tmp"]


def test_concurrent_saves_cannot_duplicate_clipboard(tmp_path, monkeypatch):
    async def fake_run(*args, timeout=2):
        if args[0] == "/usr/bin/pbpaste":
            return "copied by the app\n"
        await asyncio.sleep(0.01)  # the git lookup yields, as it does for real
        return None

    config = th.load_config(str(tmp_path / "missing.toml"))
    config["notes_dir"] = str(tmp_path / "notes")
    monkeypatch.setattr(th, "run", fake_run)
    monkeypatch.setattr(th, "load_config", lambda: config)

    notes = th.NoteTaker(connection=None)
    session = FakeSession(str(tmp_path))

    async def two_quick_presses():
        await asyncio.gather(notes.save(session), notes.save(session))

    asyncio.run(two_quick_presses())
    [name] = os.listdir(tmp_path / "notes")
    assert (tmp_path / "notes" / name).read_text().count("> copied by the app") == 1



@pytest.mark.parametrize("text, expected", [
    ("  line\n\tline", ["  line", "\tline"]),       # no shared prefix: untouched
    ("\t a\n\t b\n\t\tc", [" a", " b", "\tc"]),  # only the shared "\t" goes
    ("    a\n  b", ["  a", "b"]),
])
def test_clean_lines_mixed_indentation(text, expected):
    assert th.clean_lines(text) == expected


@pytest.mark.parametrize("template, message", [
    ("{titel}_{id}", "{titel}"),
    ("{title}_{0}", "{0}"),
    ("{title", "unmatched"),
    ("title}", "unmatched"),
    ("{title:>10}_{id}", "{title:>10}"),
    ("{title!r}_{id}", "{title!r}"),
    ("{{title}}_{id}", "unmatched"),
    ("[term]-{title}_{id}", "* ? [ or ]"),
    ("archive/{title}_{id}", "/ or \\"),
    ("../{title}_{id}", "/ or \\"),
    ("..\\{title}_{id}", "/ or \\"),
])
def test_bad_file_name_template_is_rejected(tmp_path, template, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        th.check_file_name(template)
    path = tmp_path / "config.toml"
    path.write_text(f"file_name = {template!r}\n".replace("'", '"'))
    with pytest.raises(ValueError):
        th.load_config(str(path))


def test_good_file_name_templates():
    assert th.check_file_name("{title}") == "{title}_{id}"
    assert th.check_file_name("{dir}-{title}-{id}") == "{dir}-{title}-{id}"


def test_rename_never_replaces_an_existing_file(tmp_path):
    src, dst = tmp_path / "old.md", tmp_path / "new.md"
    src.write_text("mine")
    dst.write_text("someone else's")
    assert th.rename_no_clobber(str(src), str(dst)) == str(src)
    assert src.read_text() == "mine" and dst.read_text() == "someone else's"
    dst.unlink()
    assert th.rename_no_clobber(str(src), str(dst)) == str(dst)
    assert dst.read_text() == "mine" and not src.exists()


def test_ambiguous_matches_are_not_renamed(tmp_path):
    older = tmp_path / "Old-title_934051cd9b3f4e91.md"
    newer = tmp_path / "Other-title_934051cd9b3f4e91.md"
    older.write_text("a")
    newer.write_text("b")
    os.utime(older, (1_000_000, 1_000_000))
    path = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Brand new", None)
    assert path == str(newer)
    assert sorted(os.listdir(tmp_path)) == [older.name, newer.name]  # nothing renamed or lost


def test_failed_action_shows_an_alert(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('file_name = "{titel}"\n')
    real_load = th.load_config
    monkeypatch.setattr(th, "load_config", lambda: real_load(str(path)))
    alerts = []

    class Notes(th.NoteTaker):
        async def alert(self, session, title, message):
            alerts.append((title, message))

    asyncio.run(Notes(connection=None).perform("save", FakeSession(str(tmp_path))))
    [(title, message)] = alerts
    assert title == "term-notes: save failed"
    assert "{titel}" in message



def test_load_bindings_reports_problems_and_falls_back(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('file_name = "{titel}"\n')
    bindings, errors = th.load_bindings(lambda: th.load_config(str(path)))
    assert len(errors) == 1 and "{titel}" in errors[0]
    assert sorted(name for name, _ in bindings.values()) == sorted(th.DEFAULTS["keys"])

    path.write_text('[keys]\nsave = "ctrl+alt+f5"\nbogus = "ctrl+alt+b"\n')
    bindings, errors = th.load_bindings(lambda: th.load_config(str(path)))
    assert any("f5" in e for e in errors) and any("bogus" in e for e in errors)
    names = {name for name, _ in bindings.values()}
    assert "save" not in names and "undo" in names  # the rest still work


def test_clipboard_state_is_a_digest_and_forgotten(tmp_path, monkeypatch):
    async def fake_run(*args, timeout=2):
        return "a large selection\n" if args[0] == "/usr/bin/pbpaste" else None

    config = th.load_config(str(tmp_path / "missing.toml"))
    config["notes_dir"] = str(tmp_path / "notes")
    monkeypatch.setattr(th, "run", fake_run)
    monkeypatch.setattr(th, "load_config", lambda: config)
    notes = th.NoteTaker(connection=None)
    asyncio.run(notes.save(FakeSession(str(tmp_path))))
    stored = notes.last_clipboard[SESSION]
    assert stored == th.digest("a large selection\n") and "selection" not in stored
    assert SESSION in notes.locks

    notes.forget(SESSION)
    assert SESSION not in notes.last_clipboard and SESSION not in notes.locks


def test_forget_keeps_state_while_an_action_runs():
    notes = th.NoteTaker(connection=None)

    async def action():
        notes.forget(SESSION)
        assert SESSION in notes.locks and SESSION in notes.closed

    asyncio.run(notes.exclusive(SESSION, action))
    assert SESSION not in notes.locks and not notes.closed  # purged afterwards



def test_session_ids_keep_64_bits():
    assert th.short_id(SESSION) == "934051cd9b3f4e91"
    # Differ only after the first 32 bits: still separate files.
    assert th.short_id("934051CD-0000-0000-0000-000000000000") != th.short_id(SESSION)


def test_session_closed_during_save_is_purged_afterwards(tmp_path, monkeypatch):
    release = None

    async def fake_run(*args, timeout=2):
        if args[0] == "/usr/bin/pbpaste":
            return "copied\n"
        await release.wait()  # the git lookup is still running...
        return None

    config = th.load_config(str(tmp_path / "missing.toml"))
    config["notes_dir"] = str(tmp_path / "notes")
    monkeypatch.setattr(th, "run", fake_run)
    monkeypatch.setattr(th, "load_config", lambda: config)
    notes = th.NoteTaker(connection=None)

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        save = asyncio.create_task(notes.save(FakeSession(str(tmp_path))))
        while not (SESSION in notes.locks and notes.locks[SESSION].locked()):
            await asyncio.sleep(0)
        notes.forget(SESSION)  # ...when the session closes
        release.set()
        await save

    asyncio.run(scenario())
    assert os.listdir(tmp_path / "notes")  # the in-flight save still finished
    assert SESSION not in notes.last_clipboard
    assert SESSION not in notes.locks
    assert not notes.closed


def test_conflicting_shortcuts_are_all_disabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[keys]\nsave = "alt+ctrl+z"\n')  # same as undo's ctrl+alt+z
    bindings, errors = th.load_bindings(lambda: th.load_config(str(path)))
    names = {name for name, _ in bindings.values()}
    assert "save" not in names and "undo" not in names
    assert names == {"save_with_comment", "open_notes"}
    [error] = errors
    assert "save (alt+ctrl+z)" in error and "undo (ctrl+alt+z)" in error



def test_legacy_8_character_file_is_migrated(tmp_path):
    legacy = tmp_path / "Old-title_934051cd.md"
    legacy.write_text("## old note\n\n")
    path = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "New title", None)
    assert os.path.basename(path) == "New-title_934051cd9b3f4e91.md"
    assert os.listdir(tmp_path) == ["New-title_934051cd9b3f4e91.md"]
    with open(path) as f:
        assert f.read() == "## old note\n\n"


def test_legacy_lookup_only_when_no_current_file(tmp_path):
    (tmp_path / "Old_934051cd.md").write_text("legacy")
    current = tmp_path / "Now_934051cd9b3f4e91.md"
    current.write_text("current")
    path = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Now", None)
    assert path == str(current)
    assert (tmp_path / "Old_934051cd.md").read_text() == "legacy"  # left alone


def test_ambiguous_legacy_files_are_not_renamed(tmp_path):
    a, b = tmp_path / "A_934051cd.md", tmp_path / "B_934051cd.md"
    a.write_text("a")
    b.write_text("b")
    os.utime(a, (1_000_000, 1_000_000))
    assert th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "New", None) == str(b)
    assert sorted(os.listdir(tmp_path)) == ["A_934051cd.md", "B_934051cd.md"]


def test_rename_without_hard_links_keeps_old_name(tmp_path, monkeypatch):
    src, dst = tmp_path / "old.md", tmp_path / "new.md"
    src.write_text("mine")

    def no_links(a, b):
        raise PermissionError("hard links not supported")

    monkeypatch.setattr(th.os, "link", no_links)
    assert th.rename_no_clobber(str(src), str(dst)) == str(src)
    assert src.read_text() == "mine" and not dst.exists()


def test_failed_append_leaves_no_partial_note(tmp_path, monkeypatch):
    path = tmp_path / "n.md"
    th.append_note(str(path), th.format_note(["one"], when=WHEN))
    before = path.read_bytes()

    def disk_full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(th.os, "fsync", disk_full)
    with pytest.raises(OSError):
        th.append_note(str(path), th.format_note(["two " * 1000], when=WHEN))
    assert path.read_bytes() == before


def test_queued_action_after_close_is_cleaned_up(tmp_path, monkeypatch):
    release = None

    async def fake_run(*args, timeout=2):
        if args[0] == "/usr/bin/pbpaste":
            return "copied\n"
        await release.wait()
        return None

    config = th.load_config(str(tmp_path / "missing.toml"))
    config["notes_dir"] = str(tmp_path / "notes")
    monkeypatch.setattr(th, "run", fake_run)
    monkeypatch.setattr(th, "load_config", lambda: config)
    notes = th.NoteTaker(connection=None)
    session = FakeSession(str(tmp_path))

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        first = asyncio.create_task(notes.save(session))
        second = asyncio.create_task(notes.save(session))  # queued behind the first
        while notes.active[SESSION] < 2:
            await asyncio.sleep(0)
        notes.forget(SESSION)
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    [name] = os.listdir(tmp_path / "notes")
    assert (tmp_path / "notes" / name).read_text().count("> copied") == 1
    assert SESSION not in notes.last_clipboard
    assert SESSION not in notes.locks and SESSION not in notes.active
    assert not notes.closed



def test_failed_first_append_leaves_no_file(tmp_path, monkeypatch):
    path = tmp_path / "new.md"

    def disk_full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(th.os, "fsync", disk_full)
    with pytest.raises(OSError):
        th.append_note(str(path), th.format_note(["first"], when=WHEN))
    assert not path.exists()
