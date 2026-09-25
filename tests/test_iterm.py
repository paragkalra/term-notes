import asyncio
import datetime
import os
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
    assert os.path.basename(path) == "Claude-Code_934051cd.md"
    assert not os.path.exists(path)  # only named, created on first note


def test_notes_file_renamed_when_title_changes(tmp_path):
    first = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Claude Code", None)
    th.append_note(first, "## note\n\n")
    second = th.notes_file_for(str(tmp_path), "{title}_{id}", SESSION, "Fix login bug", None)
    assert os.path.basename(second) == "Fix-login-bug_934051cd.md"
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
    assert os.path.basename(path) == "my-repo-T_934051cd.md"


def test_notes_dir_with_glob_characters(tmp_path):
    notes_dir = tmp_path / "notes [work]"
    first = th.notes_file_for(str(notes_dir), "{title}_{id}", SESSION, "One", None)
    th.append_note(first, "## x\n\n")
    second = th.notes_file_for(str(notes_dir), "{title}_{id}", SESSION, "Two", None)
    assert os.path.exists(second) and not os.path.exists(first)


def test_clean_lines():
    text = "  ⏺ The fix is in auth.ts   \n\n   \n  line two (see #42).   \n"
    assert th.clean_lines(text) == ["  ⏺ The fix is in auth.ts", "  line two (see #42)."]
    assert th.clean_lines(None) == []
    assert th.clean_lines("\n  \n") == []


def test_format_note_full():
    home = os.path.expanduser("~")
    note = th.format_note(["  first line", "second"], when=WHEN, title="✳ Claude Code",
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


def test_keystroke_patterns_forbid_other_modifiers():
    [pattern] = th.keystroke_patterns([th.parse_key("ctrl+alt+h")])
    assert set(pattern.required_modifiers) == {iterm2.Modifier.CONTROL, iterm2.Modifier.OPTION}
    assert set(pattern.forbidden_modifiers) == {iterm2.Modifier.SHIFT, iterm2.Modifier.COMMAND}
    assert pattern.keycodes == [iterm2.Keycode.ANSI_H]
