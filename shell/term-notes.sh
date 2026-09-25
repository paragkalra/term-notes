# term-notes shell integration (zsh and bash).
#
# At every prompt, tells the terminal (iTerm2 or WezTerm) which host,
# directory, git repo and branch this shell is in, so notes saved from SSH
# sessions record where you were on the remote machine. Source it from
# ~/.zshrc or ~/.bashrc on every machine you SSH into -- and on your Mac
# too, so the values stay current after you log out of a remote host:
#
#   source ~/term-notes.sh
#
# It sets terminal "user variables" with the OSC 1337 SetUserVar escape
# sequence, which both iTerm2 and WezTerm understand. Inside tmux, enable
# passthrough so they reach the terminal: set -g allow-passthrough on

__term_notes_set() {
  # OSC 1337 ; SetUserVar=<name>=<base64 value> BEL
  local value
  value=$(printf '%s' "$2" | base64 | tr -d '\n')
  if [ -n "$TMUX" ]; then
    printf '\033Ptmux;\033\033]1337;SetUserVar=%s=%s\007\033\\' "$1" "$value"
  else
    printf '\033]1337;SetUserVar=%s=%s\007' "$1" "$value"
  fi
}

__term_notes_publish() {
  local dir="$PWD" top repo="" branch=""
  case "$dir" in
    "$HOME") dir="~" ;;
    "$HOME"/*) dir="~${dir#"$HOME"}" ;;
  esac
  if top=$(git rev-parse --show-toplevel 2>/dev/null); then
    repo=${top##*/}
    branch=$(git branch --show-current 2>/dev/null)
    [ -n "$branch" ] || branch=detached
  fi
  __term_notes_set term_notes_host "$(hostname -s 2>/dev/null || hostname)"
  __term_notes_set term_notes_dir "$dir"
  __term_notes_set term_notes_repo "$repo"
  __term_notes_set term_notes_branch "$branch"
}

if [ -n "$ZSH_VERSION" ]; then
  autoload -Uz add-zsh-hook && add-zsh-hook precmd __term_notes_publish
elif [ -n "$BASH_VERSION" ]; then
  case ";${PROMPT_COMMAND:-};" in
    *";__term_notes_publish;"*) ;;
    *) PROMPT_COMMAND="__term_notes_publish${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
  esac
fi
