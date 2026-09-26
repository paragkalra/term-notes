# Changelog

## 0.3.3

- Correction to 0.3.1: SSH git context does **not** work inside zellij,
  which drops the escape sequences the snippet uses. Its values are no
  longer trusted, so stale ones can't end up in notes.
- tmux and GNU Screen nested inside each other are documented as
  unsupported: the environment can't tell which runs inside which, and each
  order needs different wrapping.

## 0.3.2

- The shell snippet works inside GNU Screen: its values are wrapped in
  Screen's pass-through string so they reach the terminal (0.3.1 trusted
  Screen but the values didn't get through).
- WezTerm: after a message clears, both `update-status` (current) and
  `update-right-status` (older configs) are emitted, so either kind of
  status handler redraws right away.

## 0.3.1

- SSH git context now works when the terminal runs tmux, screen or zellij
  (with ssh inside it), not only when ssh itself is in the foreground.
- ⌃⌥N in a shell-named tab opens its pre-0.3 `zsh.md`/`bash.md` notes when
  the tab has no directory-named file yet. Those notes aren't moved, since
  one `zsh.md` can mix several directories.
- The shell snippet keeps the previous command's exit status, so prompt
  hooks that run after it (and `$?` in the prompt) see the real value.
- WezTerm: tab-bar messages are tracked per window, so a message in one
  window no longer leaves another window's message stuck; after a message,
  a config's own `update-right-status` handler is asked to redraw.

## 0.3.0

- **Git context over SSH.** Source `shell/term-notes.sh` on the machines you
  SSH into: notes then record the remote host, directory, repo and branch
  (e.g. `pkbox:~/proj` · `proj` @ `feat/x`). Works in iTerm2 and WezTerm,
  and inside tmux with `allow-passthrough`.
- **Unnamed tabs get useful file names.** A tab titled just `zsh`, `bash`,
  `ssh`... uses its directory's name, instead of every unnamed tab sharing
  `zsh.md`.
- WezTerm: messages ("Nothing selected", "Removed last note", errors) show
  on the right of the tab bar, since macOS often hides notifications from
  the app in front.

## 0.2.0

- **One notes file per tab title** is the new default (`file_name =
  "{title}"`): reopening a tab or reconnecting SSH keeps using the same file.
  `"{title}_{id}"` keeps one file per tab that follows title changes.
- Upgrading merges files from the old per-tab naming into the shared file,
  oldest first; only files from before the upgrade are merged.
- Empty terminal cells (NUL) and no-break spaces in selections are saved as
  spaces, so Claude Code output no longer loses its spaces.

## 0.1.0

- First release: save selected text as Markdown notes from iTerm2 and
  WezTerm, with comments, open, undo, git context and a TOML config.
- Works with agent TUIs (Claude Code, Codex) through a clipboard fallback.
- Hardened through several review rounds: atomic undo, rollback of failed
  writes, locking between term-notes processes, safe renames and more (see
  the git history).
