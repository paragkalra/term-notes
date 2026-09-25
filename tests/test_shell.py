"""shell/term-notes.sh: the prompt hook that publishes host, directory and
git repo/branch as terminal user variables."""
import base64
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNIPPET = os.path.join(ROOT, "shell", "term-notes.sh")
SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]
SET_USER_VAR = re.compile(r"\x1b\]1337;SetUserVar=(\w+)=([A-Za-z0-9+/=]*)\x07")


def publish(shell, cwd, home, env=None):
    """Run the prompt hook once in `cwd` and return the raw output."""
    script = f'source "{SNIPPET}"; cd "{cwd}"; __term_notes_publish'
    full_env = {"PATH": os.environ["PATH"], "HOME": str(home), **(env or {})}
    return subprocess.run([shell, "-c", script], capture_output=True, text=True,
                          env=full_env, check=True).stdout


def user_vars(output):
    return {name: base64.b64decode(value).decode() for name, value in SET_USER_VAR.findall(output)}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize("shell", SHELLS)
def test_publishes_repo_branch_and_home_relative_dir(shell, tmp_path):
    repo = tmp_path / "home" / "src" / "my repo"
    (repo / "sub").mkdir(parents=True)
    git("init", "-q", "-b", "feat/x", cwd=repo)  # no commits yet: still has a branch
    found = user_vars(publish(shell, repo / "sub", tmp_path / "home"))
    assert found["term_notes_dir"] == "~/src/my repo/sub"
    assert found["term_notes_repo"] == "my repo"
    assert found["term_notes_branch"] == "feat/x"
    assert found["term_notes_host"]


@pytest.mark.parametrize("shell", SHELLS)
def test_outside_a_repo_clears_repo_and_branch(shell, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    found = user_vars(publish(shell, home, home))
    assert found["term_notes_dir"] == "~"
    assert found["term_notes_repo"] == "" and found["term_notes_branch"] == ""


@pytest.mark.parametrize("shell", SHELLS)
def test_detached_head(shell, tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x", cwd=repo)
    git("checkout", "-q", "--detach", cwd=repo)
    assert user_vars(publish(shell, repo, tmp_path))["term_notes_branch"] == "detached"


@pytest.mark.parametrize("shell", SHELLS)
def test_tmux_passthrough(shell, tmp_path):
    out = publish(shell, tmp_path, tmp_path, env={"TMUX": "/tmp/tmux-1/default,1,0"})
    assert out.startswith("\x1bPtmux;\x1b\x1b]1337;SetUserVar=term_notes_host=")
    assert out.count("\x1b\\") == 4  # one wrapped sequence per variable


def test_zsh_registers_a_precmd_hook(tmp_path):
    if "zsh" not in SHELLS:
        pytest.skip("zsh not installed")
    out = subprocess.run(["zsh", "-c", f'source "{SNIPPET}"; source "{SNIPPET}"; print -l $precmd_functions'],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out.count("__term_notes_publish") == 1


def test_bash_adds_itself_to_prompt_command_once():
    out = subprocess.run(["bash", "-c", f'PROMPT_COMMAND="history -a"; source "{SNIPPET}"; '
                          f'source "{SNIPPET}"; printf %s "$PROMPT_COMMAND"'],
                         capture_output=True, text=True, check=True).stdout
    assert out == "__term_notes_publish;history -a"
