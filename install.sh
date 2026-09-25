#!/bin/sh
# Install term-notes for iTerm2 (macOS).
set -eu

repo_dir=$(cd "$(dirname "$0")" && pwd)
autolaunch="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
config_dir="$HOME/.config/term-notes"

mkdir -p "$autolaunch" "$config_dir"
ln -sf "$repo_dir/iterm/term_notes.py" "$autolaunch/term_notes.py"
echo "Linked $autolaunch/term_notes.py"

if [ -e "$config_dir/config.toml" ]; then
  echo "Kept existing $config_dir/config.toml"
else
  cp "$repo_dir/config.example.toml" "$config_dir/config.toml"
  echo "Created $config_dir/config.toml"
fi

cat <<'EOF'

Next, in iTerm2:
  1. Settings > General > Magic > Enable Python API
  2. Scripts > Manage > Install Python Runtime (if not installed)
  3. Scripts > AutoLaunch > term_notes.py (starts it now; later it starts automatically)

Then select text and press Ctrl+Option+H.
EOF
