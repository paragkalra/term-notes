import glob
import importlib.util
import os
import subprocess

import lupa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_iterm_module():
    spec = importlib.util.spec_from_file_location(
        "term_notes", os.path.join(ROOT, "iterm", "term_notes.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_wezterm_plugin(home="/Users/me"):
    """Load plugin/init.lua in Lua with a minimal stub of the wezterm module.

    Returns (lua runtime, plugin module table, stub table, fake). Set
    fake["clipboard"] to control what the plugin reads from the clipboard,
    add command names to fake["fail"] to make them fail, set
    fake["before"][command] to a function to run once when that command
    runs (as if something else ran while it yielded), and read the commands
    that ran from fake["calls"].
    """
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    fake = {"clipboard": None, "calls": [], "fail": set(), "before": {}}

    def run_child_process(args):
        argv = [args[i] for i in range(1, len(args) + 1)]
        fake["calls"].append(argv)
        hook = fake["before"].pop(argv[0], None)
        if hook:  # simulates other work running while this command yields
            hook()
        if argv[0] in fake["fail"]:
            return False, "", "failed on purpose"
        if argv[0] in ("pbpaste", "wl-paste", "xclip"):
            clip = fake["clipboard"]
            return clip is not None, clip or "", ""
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        except OSError:
            return False, "", ""
        return proc.returncode == 0, proc.stdout, proc.stderr

    def glob_(pattern):
        return lua.table_from(sorted(glob.glob(pattern)))

    stub = lua.eval("""
        function(home, run_child_process, glob)
          local stub = {
            home_dir = home,
            target_triple = 'aarch64-apple-darwin',
            errors = {},
            closed_panes = {},
            GLOBAL = {},
            logs = {},
            run_child_process = run_child_process,
            glob = glob,
            action = setmetatable({}, { __index = function(_, name)
              return function(args) return { action = name, args = args } end
            end }),
          }
          function stub.action_callback(fn) return { callback = fn } end
          stub.timers = {}
          stub.time = { call_after = function(_, fn) table.insert(stub.timers, fn) end }
          function stub.format(items)
            local out = {}
            for _, item in ipairs(items) do out[#out + 1] = item.Text or '' end
            return table.concat(out)
          end
          function stub.log_info(msg) table.insert(stub.logs, msg) end
          function stub.log_error(msg) table.insert(stub.errors, msg) end
          function stub.log_warn(msg) table.insert(stub.logs, msg) end
          stub.mux = { get_pane = function(id)
            if stub.closed_panes[id] then return nil end
            return {}
          end }
          function stub.open_with(path) stub.opened = path end
          package.loaded.wezterm = stub
          return stub
        end
    """)(home, run_child_process, glob_)
    with open(os.path.join(ROOT, "plugin", "init.lua")) as f:
        plugin = lua.execute(f.read())
    return lua, plugin, stub, fake
