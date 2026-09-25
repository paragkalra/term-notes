import datetime
import os
import subprocess

import pytest
from conftest import load_iterm_module, load_wezterm_plugin

th = load_iterm_module()

HOME = "/Users/me"


@pytest.fixture
def wez():
    return load_wezterm_plugin(home=HOME)


def lua_list(lua, items):
    return lua.table_from(list(items))


def make_pane(lua, *, pane_id=1, tab_title="", title="zsh", cwd="/tmp", selection=""):
    """A fake WezTerm (window, pane) pair; attributes can be changed later."""
    return lua.eval("""
        function(pane_id, tab_title, title, cwd, selection)
          local state = { tab_title = tab_title, title = title, cwd = cwd,
                          selection = selection, toasts = {}, splits = {} }
          local tab = { get_title = function() return state.tab_title end }
          local pane = {
            pane_id = function() return pane_id end,
            tab = function() return tab end,
            get_title = function() return state.title end,
            get_current_working_dir = function()
              if state.cwd == nil then return nil end
              return { file_path = state.cwd }
            end,
            split = function(self, args) table.insert(state.splits, args) end,
          }
          local window = {
            get_selection_text_for_pane = function() return state.selection end,
            toast_notification = function(_, _, msg) table.insert(state.toasts, msg) end,
            perform_action = function(self, action, p) state.prompt = action end,
          }
          return window, pane, state
        end
    """)(pane_id, tab_title, title, cwd, selection)


def call(action, window, pane):
    action.callback(window, pane)


@pytest.mark.parametrize("title", [
    "✳ iTerm highlight setup", "⠐ Fix auth bug (PLAT-741)", "user@host:~", "", "✳",
])
def test_slug_matches_python_for_ascii_titles(wez, title):
    _, plugin, _, _ = wez
    assert plugin.slug(title) == th.slug(title)


@pytest.mark.parametrize("kwargs", [
    {},
    {"title": "✳ Claude Code"},
    {"cwd": HOME + "/src/app", "git": ("app", "main")},
    {"cwd": "/opt/x", "comment": "  why this matters  "},
    {"title": "T", "cwd": HOME, "git": ("r", "feat/x"), "comment": "c"},
    {"title": "task\n## subtitle", "cwd": "/a\r\n/b", "comment": " line one\n  line two "},
    {"git": ("my\n## repo", "feat\n## x")},
])
def test_note_format_matches_python(wez, monkeypatch, kwargs):
    lua, plugin, _, _ = wez
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", HOME, 1))
    lines = th.clean_lines("  first line  \n\n     indented\n  second")
    expected = th.format_note(lines, when=datetime.datetime(2026, 9, 24, 15, 43), **kwargs)
    opts = {"when": "2026-09-24 15:43", "home": HOME, **kwargs}
    if "git" in opts:
        opts["git"] = lua_list(lua, opts["git"])
    assert plugin.format_note(lua_list(lua, lines), lua.table_from(opts)) == expected


def test_without_last_note(wez):
    _, plugin, _, _ = wez
    one = "## a\n\n> x\n\n"
    two = "## b\n\n> ## quoted\n\n**Comment:** ## c\n\n"
    assert plugin.without_last_note(one + two) == one
    assert plugin.without_last_note(one) == ""
    assert plugin.without_last_note("") is None


@pytest.mark.parametrize("text", [
    "  a  \r\n\n  \nb\n",
    "\n  \n  ⏺ The fix\n\n     indented more\n  back\n\n",
    "    def f(x):\n        return 1\n\n    f(2)\n",
    "\n  \n",
    "",
])
def test_clean_lines_matches_python(wez, text):
    _, plugin, _, _ = wez
    assert list(plugin.clean_lines(text).values()) == th.clean_lines(text)


@pytest.mark.parametrize("uri, path", [
    ("file://host/Users/me/my%20repo", "/Users/me/my repo"),
    ("file:///tmp/%E2%9C%B3%20x%25y", "/tmp/✳ x%y"),
    ("file://host/plain", "/plain"),
])
def test_path_from_uri(wez, uri, path):
    _, plugin, _, _ = wez
    assert plugin.path_from_uri(uri) == path


def test_old_wezterm_string_cwd_is_decoded(wez, tmp_path):
    lua, plugin, _, _ = wez
    repo = tmp_path / "my repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
    notes = tmp_path / "notes"
    actions = plugin.actions(plugin.settings(lua.table_from(
        {"notes_dir": str(notes), "file_name": "{dir}_{id}"})))
    window, pane, _ = make_pane(lua, selection="x")
    uri = "file://host" + str(repo).replace(" ", "%20")
    pane.get_current_working_dir = lua.eval("function(uri) return function() return uri end end")(uri)
    call(actions.save, window, pane)
    [name] = os.listdir(notes)
    assert name.startswith("my-repo_")
    content = (notes / name).read_text()
    assert "`myrepo`" not in content and "`my repo` @ `trunk`" in content


def test_parse_key(wez):
    _, plugin, _, _ = wez
    binding = plugin.parse_key("Ctrl+Alt+Shift+H")
    assert (binding.key, binding.mods) == ("h", "CTRL|ALT|SHIFT")
    assert plugin.parse_key("cmd+7").mods == "SUPER"
    with pytest.raises(Exception, match="unsupported key"):
        plugin.parse_key("ctrl+f5")
    with pytest.raises(Exception, match="unknown modifier"):
        plugin.parse_key("hyper+h")


def test_settings_merge(wez):
    lua, plugin, _, _ = wez
    settings = plugin.settings(lua.eval(
        "{ file_name = '{title}', keys = { undo = false }, split_command = { 'cat' } }"))
    # (["keys"], not .keys, which is lupa's table method)
    assert settings.file_name == "{title}_{id}"
    assert settings["keys"].undo is False
    assert settings["keys"].save == "ctrl+alt+h"
    assert list(settings.split_command.values()) == ["cat"]
    assert plugin.defaults["keys"].undo == "ctrl+alt+z"  # defaults untouched


def test_apply_to_config_adds_bindings(wez):
    lua, plugin, _, _ = wez
    config = lua.eval("{ keys = { { key = 'q', mods = 'CMD' } } }")
    plugin.apply_to_config(config, lua.eval("{ keys = { undo = false } }"))
    keys = {(k.key, k.mods) for k in config["keys"].values()}
    assert keys == {("q", "CMD"), ("h", "CTRL|ALT"), ("h", "CTRL|ALT|SHIFT"), ("n", "CTRL|ALT")}


def settings_for(lua, plugin, tmp_path, **extra):
    opts = {"notes_dir": str(tmp_path), "git_context": False, **extra}
    return plugin.settings(lua.table_from(opts))


def test_save_writes_note_and_follows_title(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, tab_title="", title="✳ Claude Code",
                                    cwd=HOME + "/src", selection="  hello world  \n")
    call(actions.save, window, pane)
    [first] = os.listdir(tmp_path)
    assert first.startswith("Claude-Code_") and first.endswith(".md")
    content = (tmp_path / first).read_text()
    assert content.endswith("· ✳ Claude Code\n\n`~/src`\n\n> hello world\n\n")

    state.title = "Fix login bug"
    state.selection = "second"
    call(actions.save, window, pane)
    [renamed] = os.listdir(tmp_path)
    assert renamed.startswith("Fix-login-bug_")
    assert (tmp_path / renamed).read_text().count("## ") == 2


def test_explicit_tab_title_wins(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, _ = make_pane(lua, tab_title="Notes tab", title="zsh", selection="x")
    call(actions.save, window, pane)
    assert os.listdir(tmp_path)[0].startswith("Notes-tab_")


def test_clipboard_fallback_and_dedupe(wez, tmp_path):
    lua, plugin, _, fake = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="")
    fake["clipboard"] = "copied by the app"
    call(actions.save, window, pane)
    call(actions.save, window, pane)  # same clipboard again: skipped
    [name] = os.listdir(tmp_path)
    assert (tmp_path / name).read_text().count("> copied by the app") == 1
    assert list(state.toasts.values()) == ["Nothing selected"]


def test_save_with_comment(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="the text")
    call(actions.save_with_comment, window, pane)
    assert state.prompt.action == "PromptInputLine"
    state.prompt.args.action.callback(window, pane, "my comment")
    [name] = os.listdir(tmp_path)
    assert (tmp_path / name).read_text().endswith("> the text\n\n**Comment:** my comment\n\n")


def test_save_with_comment_cancelled(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="the text")
    call(actions.save_with_comment, window, pane)
    state.prompt.args.action.callback(window, pane, None)
    assert os.listdir(tmp_path) == []


def test_undo(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="one")
    call(actions.save, window, pane)
    state.selection = "two"
    call(actions.save, window, pane)
    call(actions.undo, window, pane)
    [name] = os.listdir(tmp_path)
    content = (tmp_path / name).read_text()
    assert "> one" in content and "> two" not in content
    call(actions.undo, window, pane)
    assert os.listdir(tmp_path) == []
    call(actions.undo, window, pane)
    assert list(state.toasts.values())[-1] == "No notes to undo"


def test_panes_get_separate_files(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    w1, p1, _ = make_pane(lua, pane_id=1, title="Same", selection="a")
    w2, p2, _ = make_pane(lua, pane_id=2, title="Same", selection="b")
    call(actions.save, w1, p1)
    call(actions.save, w2, p2)
    assert len(os.listdir(tmp_path)) == 2


def test_open_notes(wez, tmp_path):
    lua, plugin, stub, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="x")
    call(actions.open_notes, window, pane)
    assert list(state.toasts.values()) == ["No notes yet for this tab"]
    call(actions.save, window, pane)
    call(actions.open_notes, window, pane)
    [split] = state.splits.values()
    args = list(split.args.values())
    assert args[:3] == ["less", "-R", "+G"] and args[3].startswith(str(tmp_path))

    app_actions = plugin.actions(settings_for(lua, plugin, tmp_path, open_in="app"))
    call(app_actions.open_notes, window, pane)
    assert stub.opened == args[3]


def test_git_context(wez, tmp_path):
    lua, plugin, _, _ = wez
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
    notes = tmp_path / "notes"
    actions = plugin.actions(plugin.settings(lua.table_from({"notes_dir": str(notes)})))
    window, pane, _ = make_pane(lua, cwd=str(repo), selection="x")
    call(actions.save, window, pane)
    [name] = os.listdir(notes)
    assert "`myrepo` @ `trunk`" in (notes / name).read_text()


# --- Review fixes -----------------------------------------------------------

def test_undo_keeps_notes_when_rewrite_fails(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="one")
    call(actions.save, window, pane)
    state.selection = "two"
    call(actions.save, window, pane)
    [name] = os.listdir(tmp_path)
    before = (tmp_path / name).read_text()
    tmp_path.chmod(0o555)  # the temp file can't be created
    try:
        call(actions.undo, window, pane)
    finally:
        tmp_path.chmod(0o755)
    assert (tmp_path / name).read_text() == before  # nothing lost
    assert os.listdir(tmp_path) == [name]
    assert list(state.toasts.values())[-1].startswith("Could not update")


def test_multiline_title_cannot_fake_a_note_boundary(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, title="plain", selection="one")
    call(actions.save, window, pane)
    [name] = os.listdir(tmp_path)
    first = (tmp_path / name).read_text()
    state.title, state.selection = "plain", "two"
    state.tab_title = "task\n## subtitle"
    call(actions.save, window, pane)
    [name] = os.listdir(tmp_path)
    call(actions.undo, window, pane)
    assert (tmp_path / name).read_text() == first


def test_clipboard_can_be_retried_after_failed_save(wez, tmp_path):
    lua, plugin, _, fake = wez
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    broken = plugin.actions(settings_for(lua, plugin, blocker / "notes"))
    window, pane, state = make_pane(lua, selection="")
    fake["clipboard"] = "copied by the app"
    call(broken.save, window, pane)
    assert list(state.toasts.values())[-1].startswith("Could not write")

    notes = tmp_path / "notes"
    working = plugin.actions(settings_for(lua, plugin, notes))
    call(working.save, window, pane)  # retry works: not treated as already saved
    call(working.save, window, pane)  # same clipboard again: skipped
    [name] = os.listdir(notes)
    assert (notes / name).read_text().count("> copied by the app") == 1


def test_concurrent_save_cannot_duplicate_clipboard(wez, tmp_path):
    # run_child_process yields in real WezTerm, so another save can run in the
    # middle of one. Simulate that by saving again from inside the first save.
    lua, plugin, _, fake = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="", cwd=str(tmp_path))
    fake["clipboard"] = "copied by the app"
    nested = {"done": False}
    real_cwd = pane.get_current_working_dir

    def cwd_with_nested_save(p):
        if not nested["done"]:
            nested["done"] = True
            call(actions.save, window, pane)
        return real_cwd(p)

    pane.get_current_working_dir = cwd_with_nested_save
    call(actions.save, window, pane)
    [name] = os.listdir(tmp_path)
    assert (tmp_path / name).read_text().count("> copied by the app") == 1
    assert "Nothing selected" in list(state.toasts.values())

    # The claim is released afterwards: a new clipboard value saves normally.
    fake["clipboard"] = "something else"
    call(actions.save, window, pane)
    assert (tmp_path / name).read_text().count("> something else") == 1


def test_cancelled_comment_releases_clipboard(wez, tmp_path):
    lua, plugin, _, fake = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="")
    fake["clipboard"] = "copied by the app"
    call(actions.save_with_comment, window, pane)
    state.prompt.args.action.callback(window, pane, None)  # Esc
    call(actions.save, window, pane)
    [name] = os.listdir(tmp_path)
    assert "> copied by the app" in (tmp_path / name).read_text()


def test_undo_preserves_permissions_and_existing_tmp(wez, tmp_path):
    lua, plugin, _, _ = wez
    actions = plugin.actions(settings_for(lua, plugin, tmp_path))
    window, pane, state = make_pane(lua, selection="one")
    call(actions.save, window, pane)
    state.selection = "two"
    call(actions.save, window, pane)
    [name] = os.listdir(tmp_path)
    notes = tmp_path / name
    notes.chmod(0o600)
    stray = tmp_path / (name + ".tmp")
    stray.write_text("someone else's file")
    call(actions.undo, window, pane)
    assert "> two" not in notes.read_text()
    assert notes.stat().st_mode & 0o777 == 0o600
    assert stray.read_text() == "someone else's file"
    assert sorted(os.listdir(tmp_path)) == sorted([name, stray.name])


def test_windows_adds_no_shortcuts(wez):
    lua, plugin, stub, _ = wez
    stub.target_triple = "x86_64-pc-windows-msvc"
    config = lua.eval("{}")
    plugin.apply_to_config(config, None)
    assert len(config["keys"]) == 0
    assert "Windows is not supported" in stub.errors[1]
