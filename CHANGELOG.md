# Changelog

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
