# term-notes

**Save the important bits of your terminal as notes.** Select text in any
tab and press <kbd>⌃⌥H</kbd>. It is saved to a Markdown notes file for that
tab, with the time, directory and git branch.

Built for long sessions with coding agents like Claude Code and Codex, where
the important bits scroll away fast. It works with anything that runs in a
terminal: shells, SSH sessions, REPLs, log tails.

```markdown
## 2026-09-24 15:43 · ✳ Fix login bug

`~/src/webapp` · `webapp` @ `fix/login`

> The session cookie is set before the redirect, so Safari drops it.

**Comment:** root cause, check the other redirect handlers too
```

## Features

| Shortcut | Action |
|---|---|
| <kbd>⌃⌥H</kbd> | Save the selection as a note |
| <kbd>⌃⌥⇧H</kbd> | Save the selection with a comment |
| <kbd>⌃⌥N</kbd> | Open this tab's notes in a split pane |
| <kbd>⌃⌥Z</kbd> | Undo: remove the last note from this tab |

- **One notes file per tab title.** Notes go to a file named after the tab
  title, so reopening a tab or reconnecting an SSH session keeps adding to
  the same file. Agents like Claude Code set the title to the current task,
  so file names describe what you were doing. A tab whose title is just a
  shell name (`zsh`, `bash`, `ssh`...) uses its directory's name instead, so
  unnamed tabs don't all share `zsh.md`. (Notes saved there before 0.3 stay
  in `zsh.md`, since it can mix several directories; <kbd>⌃⌥N</kbd> opens it
  until the tab has a new file.) Prefer one file per tab that
  follows title changes? Set `file_name = "{title}_{id}"`.

  **Upgrading from 0.1**, whose default was one file per tab
  (`Title_<id>.md`): the next time you save or open notes in a tab, older
  files with the same title are merged into `Title.md`, oldest first. Only
  files from before the upgrade are merged; a hidden `.term-notes-migration`
  file in the notes folder records when that was.
- **Keeps the text's shape.** Blank lines and indentation inside the
  selection are kept, so code and stack traces survive intact.
- **Works with agent TUIs.** Claude Code, Codex and similar tools handle the
  mouse themselves and copy selections to the clipboard. When the terminal
  has no selection, term-notes reads the clipboard instead. It skips
  text it has already saved from that tab.
- **Git context.** The repo and branch of the working directory are recorded
  with each note -- including on remote machines over SSH, with the shell
  snippet (see [SSH sessions](#ssh-sessions)).
- **Configurable.** Notes folder, file naming and shortcuts can all be
  changed.

## iTerm2

Tested with iTerm2 3.7. Uses iTerm2's Python runtime, which needs Python 3.11 or later.

1. Enable **Settings → General → Magic → Enable Python API**.
2. Install the Python runtime: **Scripts → Manage → Install Python Runtime**.
3. Install the script:
   ```sh
   git clone https://github.com/paragkalra/term-notes.git
   cd term-notes && ./install.sh
   ```
   `install.sh` symlinks `iterm/term_notes.py` into iTerm2's
   AutoLaunch folder, so `git pull` updates it. It also copies
   `config.example.toml` to `~/.config/term-notes/config.toml` if that
   file doesn't exist yet.
4. Start it once from **Scripts → AutoLaunch → term_notes.py**. After
   that it starts automatically with iTerm2.

The script handles its own shortcuts, so there's nothing to set up in iTerm2's
key bindings. If you'd rather bind keys yourself, set them to `""` in the
config. Then add bindings under **Settings → Keys → Key Bindings → Invoke
Script Function**, entering one of these calls exactly:

```
term_notes_save(session_id: id)
term_notes_save_with_comment(session_id: id)
term_notes_open_notes(session_id: id)
term_notes_undo(session_id: id)
```

## WezTerm

Add to `~/.wezterm.lua`:

```lua
local wezterm = require 'wezterm'
local config = wezterm.config_builder()

local term_notes = wezterm.plugin.require 'https://github.com/paragkalra/term-notes'
term_notes.apply_to_config(config, {
  -- any settings from the table below, e.g.
  -- notes_dir = wezterm.home_dir .. '/notes/term-notes',
  -- keys = { undo = false },
})

return config
```

The WezTerm plugin has the same shortcuts and writes the same notes format.
Comments are entered with WezTerm's input prompt, and messages such as
"Nothing selected" appear on the right of the tab bar for a few seconds.

**macOS and Linux only.** The plugin runs standard POSIX tools: `mkdir`,
`cp`, `ln`, `ls`, `dd` (with `/dev/null`), `git` for git context, `less` for the
default viewer, and a clipboard tool (`pbpaste` on macOS; `wl-paste` or
`xclip` on Linux). On Windows it logs an error and adds no shortcuts.

## SSH sessions

Over SSH, the terminal only knows your Mac's directory, so notes would say
`~` with no repo. [`shell/term-notes.sh`](shell/term-notes.sh) fixes that:
at every prompt it tells the terminal the host, directory, git repo and
branch, and notes then record, for example:

```markdown
`pkbox:~/src/webapp` · `webapp` @ `fix/login`
```

Copy it to each machine you SSH into and source it from `~/.zshrc` or
`~/.bashrc`:

```sh
scp shell/term-notes.sh pkbox:~/.term-notes.sh
ssh pkbox 'echo "source ~/.term-notes.sh" >> ~/.zshrc'   # or ~/.bashrc
```

Source it on your Mac too: the values stay set after you log out of the
remote host, and a local prompt replaces them. (term-notes ignores another
host's values unless `ssh` or `mosh` is in the foreground, so notes stay
right even without that. When tmux or screen is in the foreground, the
terminal can't see whether ssh runs inside it, so the values are trusted;
sourcing the snippet on your Mac keeps them current there.) It works in
both iTerm2 and WezTerm; inside tmux, add `set -g allow-passthrough on` to
`~/.tmux.conf` (GNU Screen needs no setup). Values are updated at
each prompt, so inside a long-running program like Claude Code they
describe where you started it.

## Configuration

iTerm2 reads `~/.config/term-notes/config.toml`, or the path in
`$TERM_NOTES_CONFIG`. WezTerm takes the same keys as a Lua table in
`apply_to_config`. Every setting is optional; see
[`config.example.toml`](config.example.toml).

| Setting | Default | |
|---|---|---|
| `notes_dir` | `~/notes/term-notes` | Where notes files are written |
| `file_name` | `{title}` | `{title}`, `{dir}`, `{id}`. Without `{id}`, tabs with the same title share a file; with it, each tab (and split pane) has its own, renamed on title changes |
| `git_context` | `true` | Record repo and branch |
| `open_in` | `"split"` | `"split"` pane or `"app"` (default `.md` app) |
| `split_command` | `less -R +G` | Viewer for the split pane (a list in Lua) |
| `keys.*` | see Features | `ctrl`/`alt`/`shift`/`cmd` + a letter or digit; `""` (`false` in Lua) disables |

In iTerm2, config changes apply on the next save. Key changes need a script
restart (**Scripts → Manage → Console**, then restart).

## Tips

- **Follow along:** <kbd>⌃⌥N</kbd> opens the notes in `less`. Press
  <kbd>F</kbd> to follow new notes as you save them, and <kbd>q</kbd> to close
  the pane.
- **Selecting in agent TUIs:** hold <kbd>⌥</kbd> while dragging to make
  iTerm2 do the selecting instead of the app. Both work.
- **Search everything:** `rg -i "cookie" ~/notes/term-notes`.

## Concurrency

Notes files are meant to be written only by term-notes. iTerm2 saves and
undos take an exclusive lock on `.term-notes.lock` in the notes folder, so
several iTerm2 windows and script restarts never lose each other's notes.
The WezTerm plugin can't take that lock (plain Lua has no file locking);
within one WezTerm, a pane's saves and undos run one at a time.

What isn't protected: editing a notes file in an editor, or saving from
WezTerm and iTerm2 into the same notes folder at the same instant. An
edit made while term-notes is writing can be lost, and a save that fails
with a disk error at that moment can leave a partial note. Edit notes files
when you aren't saving, or copy them elsewhere first.

## Limitations

- In apps that capture the mouse without copying to the clipboard (vim with
  `mouse=a`, tmux mouse mode), <kbd>⌥</kbd>-drag so the terminal makes the
  selection.
- If you press the shortcut without selecting anything in such an app,
  whatever you last copied is saved, once per pane. <kbd>⌃⌥Z</kbd> undoes it.
- In SSH sessions without the [shell snippet](#ssh-sessions), `{dir}` and
  git context describe your local machine, not the remote one.
- Without `{id}`, tabs with generic titles (like `zsh`) share one file, and
  <kbd>⌃⌥Z</kbd> removes the file's last note even if another tab with the
  same title saved it.
- With `{id}`, iTerm2 keeps a tab's id across restarts only when it restores
  the tab, so a new tab starts a new notes file.
- In file names, the WezTerm plugin keeps only ASCII letters and digits of
  the title. iTerm2 keeps letters in any language.

## Repository layout

```
iterm/term_notes.py   iTerm2 script (symlinked into AutoLaunch by install.sh)
plugin/init.lua       WezTerm plugin (WezTerm's plugin loader requires this path)
shell/term-notes.sh   prompt hook for remote (and local) shells: host, dir, git
config.example.toml   documented iTerm2 config
tests/                tests for both implementations
```

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install iterm2 pytest lupa ruff
.venv/bin/pytest          # tests both implementations (Lua via lupa)
.venv/bin/ruff check .
```

The WezTerm plugin is tested against a stub of the `wezterm` module. The
tests also check that both implementations write byte-identical notes.

## License

MIT
