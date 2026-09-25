-- term-notes for WezTerm: save selected terminal text as Markdown notes.
--
-- Usage in ~/.wezterm.lua:
--
--   local term_notes = wezterm.plugin.require 'https://github.com/paragkalra/term-notes'
--   term_notes.apply_to_config(config, { notes_dir = wezterm.home_dir .. '/notes/term-notes' })
--
-- Notes use the same format and file naming as the iTerm2 script.

local wezterm = require 'wezterm'
local act = wezterm.action

local M = {}

M.defaults = {
  notes_dir = wezterm.home_dir .. '/notes/term-notes',
  -- Placeholders: {title}, {dir}, {id}. Without {id}, panes with the same
  -- title share a file; with it, each pane has its own, renamed when its
  -- title changes.
  file_name = '{title}',
  git_context = true,
  -- 'split' opens the notes in a split pane, 'app' in the default .md app.
  open_in = 'split',
  split_command = { 'less', '-R', '+G' },
  -- Set a key to false to disable it.
  keys = {
    save = 'ctrl+alt+h',
    save_with_comment = 'ctrl+alt+shift+h',
    open_notes = 'ctrl+alt+n',
    undo = 'ctrl+alt+z',
  },
}

-- ---------------------------------------------------------------------------
-- Pure helpers
-- ---------------------------------------------------------------------------

function M.merge(defaults, overrides)
  local result = {}
  for k, v in pairs(defaults) do
    result[k] = v
  end
  for k, v in pairs(overrides or {}) do
    if type(v) == 'table' and type(defaults[k]) == 'table' and #v == 0 then
      result[k] = M.merge(defaults[k], v)
    else
      result[k] = v
    end
  end
  return result
end

local PLACEHOLDERS = { title = true, dir = true, id = true }

-- Same rules as the iTerm2 script's check_file_name: only plain {title},
-- {dir} and {id} (no format specs or escapes), no glob characters or path
-- separators.
function M.check_file_name(template)
  local unknown = {}
  for name in template:gmatch('{([^{}]*)}') do
    if not PLACEHOLDERS[name] then
      unknown[#unknown + 1] = '{' .. name .. '}'
    end
  end
  if #unknown > 0 then
    table.sort(unknown)
    error('term-notes: file_name ' .. template .. ' has unknown placeholder '
      .. table.concat(unknown, ', ') .. '; use {title}, {dir} and {id}', 0)
  end
  local literal = template:gsub('{[^{}]*}', '')
  if literal:find('[{}]') then
    error('term-notes: file_name ' .. template .. ' has an unmatched { or }', 0)
  end
  -- The template is also used as a glob pattern to find the pane's file.
  if literal:find('[%*%?%[%]]') then
    error("term-notes: file_name " .. template .. " can't contain * ? [ or ]", 0)
  end
  -- Keeps every notes file directly inside notes_dir (no "../").
  if literal:find('[/\\]') then
    error("term-notes: file_name " .. template .. " can't contain / or \\", 0)
  end
  return template
end

function M.settings(opts)
  local settings = M.merge(M.defaults, opts)
  settings.file_name = M.check_file_name(settings.file_name)
  return settings
end

-- Keep ASCII letters/digits/underscores/dots; everything else (spaces,
-- punctuation, spinner glyphs agents put in titles) becomes single dashes.
-- Explicit ranges, because %w depends on the C locale and can match
-- UTF-8 lead bytes, which would produce invalid file names.
function M.slug(text)
  local s = (text or ''):gsub('[^A-Za-z0-9_.]+', '-'):gsub('^[-.]+', ''):gsub('[-.]+$', '')
  s = s:sub(1, 80)
  if s == '' then
    return 'tab'
  end
  return s
end

-- Same as the iTerm2 script's file_title: a title that is just a shell or
-- program name says nothing about the tab (every unnamed tab would share
-- "zsh.md"), so file names use the working directory's name instead.
local GENERIC_TITLES = {
  zsh = true, bash = true, fish = true, sh = true, dash = true, ksh = true, tcsh = true,
  csh = true, nu = true, pwsh = true, login = true, ssh = true, tab = true,
}

function M.file_title(title, cwd)
  local name = M.slug(title)
  if GENERIC_TITLES[name:lower()] and cwd and cwd ~= '' then
    return M.slug(M.basename(cwd))
  end
  return name
end

-- Same as the iTerm2 script's remote_place: shell/term-notes.sh publishes
-- the directory and git branch as user variables at each prompt, so notes
-- from SSH sessions record where you were on the remote machine. Returns
-- cwd, git (git may be nil) -- or nil to look locally. Values from another
-- host are only trusted while an ssh-like program is in the foreground,
-- since they outlive the SSH session that set them.
local REMOTE_JOBS = {
  ssh = true, mosh = true, ['mosh-client'] = true, et = true, autossh = true,
  tsh = true, gcloud = true, aws = true,
}

function M.remote_place(user_vars, job, local_host)
  local host = (user_vars.term_notes_host or ''):match('^%s*(.-)%s*$')
  local dir = (user_vars.term_notes_dir or ''):match('^%s*(.-)%s*$')
  if dir == '' then
    return nil
  end
  local remote = host ~= '' and host:lower() ~= (local_host or ''):lower()
  if remote and not REMOTE_JOBS[M.basename(job or '')] then
    return nil
  end
  local repo, branch = user_vars.term_notes_repo or '', user_vars.term_notes_branch or ''
  local git = repo ~= '' and { repo, branch ~= '' and branch or 'detached' } or nil
  return remote and (host .. ':' .. dir) or dir, git
end

function M.basename(path)
  return (path or ''):gsub('/+$', ''):match('([^/]*)$') or ''
end

-- Make glob metacharacters in a path match literally, like Python's
-- glob.escape: "notes [work]" -> "notes [[]work[]]".
function M.glob_escape(path)
  return (path:gsub('[%*%?%[%]]', '[%0]'))
end

function M.render_name(template, fields)
  return (template:gsub('{(%w+)}', function(key)
    return fields[key] or ''
  end))
end

-- Same as the iTerm2 script's clean_lines: trim trailing padding and outer
-- blank lines, remove shared indentation, keep internal structure.
-- NUL characters (empty cells some terminals return for skipped columns)
-- and no-break spaces become ordinary spaces, as in the iTerm2 script.
function M.clean_lines(text)
  local lines = {}
  text = (text or ''):gsub('\0', ' '):gsub('\xC2\xA0', ' ')
  for line in (text .. '\n'):gmatch('(.-)\r?\n') do
    lines[#lines + 1] = (line:gsub('%s+$', ''))
  end
  while #lines > 0 and lines[1] == '' do
    table.remove(lines, 1)
  end
  while #lines > 0 and lines[#lines] == '' do
    table.remove(lines)
  end
  -- The whitespace prefix every line shares, character for character, so
  -- mixed tabs and spaces are never cut into.
  local indent
  for _, line in ipairs(lines) do
    if line ~= '' then
      local ws = line:match('^%s*')
      if not indent then
        indent = ws
      else
        local n = 0
        while n < #indent and n < #ws and indent:byte(n + 1) == ws:byte(n + 1) do
          n = n + 1
        end
        indent = indent:sub(1, n)
      end
    end
  end
  for i, line in ipairs(lines) do
    lines[i] = line:sub(#(indent or '') + 1)
  end
  return lines
end

function M.abbreviate_home(path, home)
  if path and home and (path == home or path:sub(1, #home + 1) == home .. '/') then
    return '~' .. path:sub(#home + 1)
  end
  return path
end

-- Metadata must stay on one line: a newline followed by "## " would look
-- like the start of another note to without_last_note.
function M.one_line(text)
  if not text then
    return text
  end
  return (text:gsub('%s*[\r\n]+%s*', ' '):match('^%s*(.-)%s*$'))
end

-- Same format as the iTerm2 script's format_note.
function M.format_note(lines, opts)
  local title, cwd, comment = M.one_line(opts.title), M.one_line(opts.cwd), M.one_line(opts.comment)
  local header = '## ' .. opts.when
  if title and title ~= '' then
    header = header .. ' · ' .. title
  end
  local parts = { header }
  local context = {}
  if cwd and cwd ~= '' then
    context[#context + 1] = '`' .. M.abbreviate_home(cwd, opts.home) .. '`'
  end
  if opts.git then
    context[#context + 1] = '`' .. M.one_line(opts.git[1]) .. '` @ `' .. M.one_line(opts.git[2]) .. '`'
  end
  if #context > 0 then
    parts[#parts + 1] = table.concat(context, ' · ')
  end
  local quoted = {}
  -- Blank lines become ">" so the quote stays one block.
  for i, line in ipairs(lines) do
    quoted[i] = ('> ' .. line):gsub('%s+$', '')
  end
  parts[#parts + 1] = table.concat(quoted, '\n')
  if comment and comment:find('%S') then
    parts[#parts + 1] = '**Comment:** ' .. comment
  end
  return table.concat(parts, '\n\n') .. '\n\n'
end

-- Returns the content with its last note ("## " line onward) removed, or nil
-- if there is no note.
function M.without_last_note(content)
  local last
  local pos = 1
  while true do
    local s = content:find('\n## ', pos, true)
    if not s then
      break
    end
    last = s + 1
    pos = s + 1
  end
  if not last and content:sub(1, 3) == '## ' then
    last = 1
  end
  if not last then
    return nil
  end
  return content:sub(1, last - 1)
end

-- WezTerm before 20240127 returns the cwd as a URI string such as
-- "file://host/my%20repo"; strip the scheme and host and percent-decode.
function M.path_from_uri(uri)
  local path = uri:gsub('^file://[^/]*', '')
  return (path:gsub('%%(%x%x)', function(hex)
    return string.char(tonumber(hex, 16))
  end))
end

-- "ctrl+alt+shift+h" -> { key = 'h', mods = 'CTRL|ALT|SHIFT' }
function M.parse_key(spec)
  local names = {
    ctrl = 'CTRL', control = 'CTRL', alt = 'ALT', opt = 'ALT', option = 'ALT',
    shift = 'SHIFT', cmd = 'SUPER', command = 'SUPER', super = 'SUPER',
  }
  local parts = {}
  for part in spec:lower():gsub('%s', ''):gmatch('[^+]+') do
    parts[#parts + 1] = part
  end
  local key = table.remove(parts)
  if not key or not key:match('^%w$') then
    error('term-notes: unsupported key in ' .. spec .. ': use a letter or digit')
  end
  local mods = {}
  for _, m in ipairs(parts) do
    if not names[m] then
      error('term-notes: unknown modifier ' .. m .. ' in ' .. spec)
    end
    mods[#mods + 1] = names[m]
  end
  -- Canonical order, so "alt+ctrl+h" and "ctrl+alt+h" compare equal.
  local order = { CTRL = 1, ALT = 2, SHIFT = 3, SUPER = 4 }
  local seen, unique = {}, {}
  for _, m in ipairs(mods) do
    if not seen[m] then
      seen[m] = true
      unique[#unique + 1] = m
    end
  end
  table.sort(unique, function(a, b) return order[a] < order[b] end)
  return { key = key, mods = table.concat(unique, '|') }
end

-- ---------------------------------------------------------------------------
-- WezTerm integration
-- ---------------------------------------------------------------------------

local function run(args)
  local ok, success, stdout = pcall(wezterm.run_child_process, args)
  if ok and success then
    return stdout
  end
  return nil
end

local function read_clipboard()
  return run { 'pbpaste' } or run { 'wl-paste', '--no-newline' } or run { 'xclip', '-selection', 'clipboard', '-o' }
end

local function cwd_of(pane)
  local cwd = pane:get_current_working_dir()
  if cwd == nil then
    return nil
  end
  if type(cwd) == 'string' then
    return M.path_from_uri(cwd)
  end
  return cwd.file_path -- already decoded
end

local function git_context(cwd)
  if not cwd then
    return nil
  end
  local toplevel = run { 'git', '-C', cwd, 'rev-parse', '--show-toplevel' }
  if not toplevel then
    return nil
  end
  -- `branch --show-current` also works before the first commit; it prints
  -- nothing on a detached HEAD.
  local branch = (run { 'git', '-C', cwd, 'branch', '--show-current' } or ''):match('^%s*(.-)%s*$')
  return { M.basename(toplevel:match('^%s*(.-)%s*$')), branch ~= '' and branch or 'detached' }
end

-- WezTerm pane ids restart from 0 with WezTerm, so give each pane a random
-- 64-bit id (kept across config reloads) to keep its notes file separate.
-- math.random(0) returns an integer with all 64 bits random.
local function pane_key(pane)
  wezterm.GLOBAL.term_notes_ids = wezterm.GLOBAL.term_notes_ids or {}
  local ids = wezterm.GLOBAL.term_notes_ids
  local key = tostring(pane:pane_id())
  if not ids[key] then
    ids[key] = string.format('%016x', math.random(0))
  end
  return ids[key]
end

local function tab_title(pane)
  local tab = pane:tab()
  local title = tab and tab:get_title() or ''
  if title == '' then
    title = pane:get_title() or ''
  end
  return title
end

-- The pane's { title, cwd, git, look_locally }, read once per action so a
-- note and its file name always agree.
local function pane_context(pane)
  local title = tab_title(pane)
  local ok_vars, user_vars = pcall(function() return pane:get_user_vars() end)
  local ok_job, job = pcall(function() return pane:get_foreground_process_name() end)
  local ok_host, host = pcall(wezterm.hostname)
  local cwd, git = M.remote_place(ok_vars and user_vars or {}, ok_job and job or nil,
    ok_host and host and host:match('^[^.]+') or '')
  if cwd then
    return { title = title, cwd = cwd, git = git }
  end
  return { title = title, cwd = cwd_of(pane), look_locally = true }
end

local function file_exists(path)
  local f = io.open(path, 'r')
  if f then
    f:close()
    return true
  end
  return false
end

-- Append `content` to `path`, writing only the new note. If the write fails
-- the file is cut back to its original size (plain Lua can't truncate, so
-- via dd: POSIX, and the same on macOS and Linux), or removed if it was new,
-- so a disk-full or interrupted write never leaves half a note behind.
local function append(path, content)
  local existed = file_exists(path)
  local f = io.open(path, 'a')
  if not f then
    return false
  end
  local size = f:seek('end')
  local ok = f:write(content)
  ok = f:close() and ok
  if ok then
    return true
  end
  -- Roll back only if nothing but our own bytes follow the original end of
  -- file (plain Lua has no file locking), so this can never discard someone
  -- else's append. The check re-reads the path dd will truncate.
  local check = size and io.open(path, 'rb')
  local tail = check and check:seek('set', size) and check:read(#content + 1) or ''
  if check then
    check:close()
  end
  if size and content:sub(1, #tail) == tail then
    run { 'dd', 'if=/dev/null', 'of=' .. path, 'bs=1', 'seek=' .. size }
  elseif size then
    wezterm.log_warn('term-notes: not rolling back ' .. path .. ': it changed during the write')
  end
  -- Plain Lua can't create a file exclusively, so "we created it" isn't
  -- certain: remove it only if it is still empty, which can never delete
  -- anyone's notes.
  if not existed then
    local check = io.open(path, 'r')
    local empty = check and check:seek('end') == 0
    if check then
      check:close()
    end
    if empty then
      os.remove(path)
    end
  end
  return false
end

-- Rename src to dst unless dst exists; returns the path now in use.
-- os.rename would silently replace dst. `ln` fails if the destination
-- exists, so link then unlink; if that isn't possible, keep the old name.
local function rename_no_clobber(src, dst)
  if not run { 'ln', src, dst } then
    return src
  end
  if not os.remove(src) then
    -- Both names would remain and make later lookups ambiguous: undo the
    -- link and keep the old name.
    if os.remove(dst) then
      wezterm.log_warn('term-notes: could not rename ' .. src .. '; keeping its name')
    else
      wezterm.log_error('term-notes: could not rename ' .. src .. ' to ' .. dst
        .. ', and could not remove ' .. dst .. ' again: both names now exist; delete one of them')
    end
    return src
  end
  return dst
end

-- Files from the per-pane "{title}_{id}" naming (the default before 0.2)
-- that belong in the shared file for this title, oldest first: every
-- "<title>_<16 hex>.md" (after a restart, panes get new ids, so the id can't
-- be relied on), plus this pane's own file under any title. Same rules as the
-- iTerm2 script's legacy_files.
-- Only files older than the migration marker (created the first time 0.2
-- runs in the folder; shared with the iTerm2 script) count, so a file made
-- after the upgrade is never mistaken for a legacy one.
local MIGRATION_MARKER = '.term-notes-migration'

local function legacy_files(notes_dir, title_slug, id)
  local marker = notes_dir .. '/' .. MIGRATION_MARKER
  if not file_exists(marker) then
    local m = io.open(marker, 'a')
    if not m then
      return {} -- can't tell old from new files: don't migrate anything
    end
    m:close()
  end
  local dir = M.glob_escape(notes_dir)
  local pattern = '^' .. title_slug:gsub('[%.%-]', '%%%0') .. '_' .. string.rep('[0-9a-f]', 16) .. '%.md$'
  local found, list = {}, {}
  local function add(path)
    if not found[path] then
      found[path] = true
      list[#list + 1] = path
    end
  end
  local ok, all = pcall(wezterm.glob, dir .. '/*_*.md')
  for _, path in ipairs(ok and all or {}) do
    if M.basename(path):find(pattern) then
      add(path)
    end
  end
  local ok_own, own = pcall(wezterm.glob, dir .. '/*_' .. id .. '.md')
  for _, path in ipairs(ok_own and own or {}) do
    add(path)
  end
  if #list == 0 then
    return list
  end
  -- Oldest first, with the marker in the listing: only what comes before it
  -- predates the upgrade. (Plain Lua can't read modification times.)
  local args = { 'ls', '-tr', marker }
  for _, path in ipairs(list) do
    args[#args + 1] = path
  end
  local older = {}
  for line in (run(args) or ''):gmatch('[^\n]+') do
    if line == marker then
      return older
    end
    older[#older + 1] = line
  end
  return {} -- ls failed: don't migrate anything
end

-- Fold legacy per-pane files into the shared file (caller holds the guard).
-- Each is first renamed to a hidden name, so if anything fails midway it
-- can't be merged a second time; that file is left for manual recovery.
function M.merge_legacy_files(notes_dir, wanted, title_slug, id)
  for _, old in ipairs(legacy_files(notes_dir, title_slug, id)) do
    local merging = notes_dir .. '/.' .. M.basename(old) .. '.merging'
    if old ~= wanted and rename_no_clobber(old, merging) == merging then
      local f = io.open(merging, 'rb')
      local data = f and f:read('a')
      if f then
        f:close()
      end
      if data and data:find('%S') and not data:find('\n\n$') then
        data = data:gsub('\n*$', '') .. '\n\n'
      end
      if not data or (data:find('%S') and not append(wanted, data)) then
        -- Never delete what couldn't be read or merged.
        wezterm.log_error('term-notes: could not merge ' .. old .. ' into ' .. wanted
          .. '; its notes are in ' .. merging)
      else
        os.remove(merging)
        wezterm.log_info('term-notes: merged ' .. old .. ' into ' .. wanted)
      end
    end
  end
end

-- The pane's notes file, renamed if the tab title changed.
-- A save passes `context` ({ title = ..., cwd = ... }) so the file name uses
-- exactly the values it records in the note, even nil ones (they could
-- change while it runs git). Without it, they are read now.
function M.notes_file(config, pane, context)
  local id = pane_key(pane)
  context = context or pane_context(pane)
  local title, cwd = context.title, context.cwd
  local fields = { title = M.file_title(title, cwd), dir = M.slug(M.basename(cwd)), id = id }
  local wanted = config.notes_dir .. '/' .. M.render_name(config.file_name, fields) .. '.md'
  if not config.file_name:find('{id}', 1, true) then
    -- Shared by every pane with this title.
    M.merge_legacy_files(config.notes_dir, wanted, fields.title, id)
    return wanted
  end
  local wildcard = M.render_name(config.file_name, { title = '*', dir = '*', id = id })
  local ok, existing = pcall(wezterm.glob, M.glob_escape(config.notes_dir) .. '/' .. wildcard .. '.md')
  if not ok or #existing == 0 then
    return wanted
  end
  for _, path in ipairs(existing) do
    if path == wanted then
      return wanted
    end
  end
  if #existing > 1 then
    -- Ambiguous (e.g. after a template change): don't rename anything, keep
    -- writing to the most recently used file.
    local args = { 'ls', '-t' }
    for _, path in ipairs(existing) do
      args[#args + 1] = path
    end
    local newest = (run(args) or ''):match('[^\n]+') or existing[1]
    wezterm.log_warn('term-notes: several notes files match this pane; using ' .. newest)
    return newest
  end
  return rename_no_clobber(existing[1], wanted)
end

-- 64-bit FNV-1a, so per-pane state holds a number instead of a whole
-- selection. (Lua integers wrap on overflow, which FNV relies on.)
function M.digest(text)
  local h = -3750763034362895579 -- 0xcbf29ce484222325
  for i = 1, #text do
    h = (h ~ text:byte(i)) * 1099511628211
  end
  return h
end

-- pane id -> digest of the last clipboard text saved from it.
local last_clipboard = {}
-- pane id -> digest of clipboard text being saved right now (possibly
-- waiting on the comment prompt). run_child_process yields, so a second key
-- press can run while a save is in progress; this stops it from saving the
-- same text again.
local pending_clipboard = {}

-- Drop state for panes that have closed. WezTerm has no pane-closed event,
-- so this runs on each save.
local function forget_closed_panes()
  local get_pane = wezterm.mux and wezterm.mux.get_pane
  if not get_pane then
    return
  end
  local function alive(id)
    local ok, pane = pcall(get_pane, tonumber(id))
    return ok and pane ~= nil
  end
  for _, state in ipairs { last_clipboard, pending_clipboard } do
    local closed = {}
    for id in pairs(state) do
      if not alive(id) then
        closed[#closed + 1] = id
      end
    end
    for _, id in ipairs(closed) do
      state[id] = nil
    end
  end
  local ids = wezterm.GLOBAL.term_notes_ids
  local ok, iter, t, init = pcall(pairs, ids or {})
  if ok and ids then
    local closed = {}
    for id in iter, t, init do
      if not alive(id) then
        closed[#closed + 1] = id
      end
    end
    for _, id in ipairs(closed) do
      ids[id] = nil
    end
  end
end

local function pane_id(pane)
  return tostring(pane:pane_id())
end

-- For tests.
M._state = { last_clipboard = last_clipboard, pending_clipboard = pending_clipboard }

-- Returns text, from_clipboard.
local function selected_text(window, pane)
  local text = window:get_selection_text_for_pane(pane)
  if text and text:find('%S') then
    return text, false
  end
  -- Apps with their own mouse handling (Claude Code, Codex, ...) copy their
  -- selection to the clipboard instead of making a terminal selection.
  text = read_clipboard()
  if not text then
    return nil, false
  end
  local id, hash = pane_id(pane), M.digest(text)
  if hash == last_clipboard[id] or hash == pending_clipboard[id] then
    return nil, false
  end
  pending_clipboard[id] = hash
  return text, true
end

local function release(pane, from_clipboard)
  if from_clipboard then
    pending_clipboard[pane_id(pane)] = nil
  end
end

-- Messages go to the right of the tab bar for a few seconds, because macOS
-- often doesn't show notifications from the app you're using (or WezTerm
-- never got notification permission). The notification is sent as well.
local status_shown = 0

local function toast(window, message)
  status_shown = status_shown + 1
  local this = status_shown
  window:set_right_status(wezterm.format { { Text = ' term-notes: ' .. message .. ' ' } })
  wezterm.time.call_after(4, function()
    if status_shown == this then -- a newer message may be showing
      window:set_right_status('')
    end
  end)
  window:toast_notification('term-notes', message, nil, 3000)
end

-- Replace `path`'s content without ever leaving it half-written: copy it
-- (`cp -p` keeps its permissions, which plain Lua can't set) to a uniquely
-- named temp file, write `content` there, then rename the temp file over
-- `path`. On any failure `path` is untouched. Used by undo, which is rare;
-- saves append instead (see append).
local function rewrite(path, content)
  local tmp = string.format('%s.%016x.tmp', path, math.random(0))
  local f = run { 'cp', '-p', path, tmp } and io.open(tmp, 'w')
  local ok = f and f:write(content)
  ok = f and f:close() and ok
  ok = ok and os.rename(tmp, path)
  if not ok then
    os.remove(tmp)
  end
  return ok
end

-- True while any save, undo or notes lookup is running. Commands (cp, ln,
-- ls, git...) yield, so without this a second action could start midway:
-- resolve a different path during a rename or merge, or append while an
-- undo is copying and replacing the file. The guard covers finding the file
-- as well as changing it, and it is global because panes with the same title
-- share files. Actions are key presses, so a second one is simply turned away.
local busy = false

local function exclusive(window, fn, ...)
  if busy then
    toast(window, 'Still saving the previous note; try again')
    return false
  end
  busy = true
  local ok, result = pcall(fn, ...)
  busy = false
  if not ok then
    error(result)
  end
  return result
end

-- Appends the note; returns true on success.
local function write_note(config, window, pane, text, from_clipboard, comment)
  local lines = M.clean_lines(text)
  -- Read once, so the note and its file name always agree.
  local context = pane_context(pane)
  local git = context.git
  if not config.git_context then
    git = nil
  elseif context.look_locally then
    git = git_context(context.cwd)
  end
  return exclusive(window, function()
    run { 'mkdir', '-p', config.notes_dir }
    local notes_file = M.notes_file(config, pane, context)
    local ok = append(notes_file, M.format_note(lines, {
      when = os.date('%Y-%m-%d %H:%M'),
      title = context.title,
      cwd = context.cwd,
      home = wezterm.home_dir,
      git = git,
      comment = comment,
    }))
    if not ok then
      toast(window, 'Could not write ' .. notes_file)
      return false
    end
    -- Only now that the note is on disk, so a failed save can be retried.
    if from_clipboard then
      last_clipboard[pane_id(pane)] = M.digest(text)
    end
    forget_closed_panes()
    wezterm.log_info('term-notes: saved ' .. #lines .. ' line(s) to ' .. notes_file)
    return true
  end)
end

-- write_note, always releasing the pending clipboard claim, even on error.
local function save(config, window, pane, text, from_clipboard, comment)
  local ok, result = pcall(write_note, config, window, pane, text, from_clipboard, comment)
  release(pane, from_clipboard)
  if not ok then
    error(result)
  end
  return result
end

function M.actions(config)
  local actions = {}

  actions.save = wezterm.action_callback(function(window, pane)
    local text, from_clipboard = selected_text(window, pane)
    if #M.clean_lines(text) == 0 then
      release(pane, from_clipboard)
      toast(window, 'Nothing selected')
      return
    end
    save(config, window, pane, text, from_clipboard)
  end)

  actions.save_with_comment = wezterm.action_callback(function(window, pane)
    local text, from_clipboard = selected_text(window, pane)
    if #M.clean_lines(text) == 0 then
      release(pane, from_clipboard)
      toast(window, 'Nothing selected')
      return
    end
    window:perform_action(act.PromptInputLine {
      description = 'Comment for this note (Enter to save, Esc to cancel)',
      action = wezterm.action_callback(function(w, p, comment)
        if comment ~= nil then
          save(config, w, p, text, from_clipboard, comment)
        else
          release(p, from_clipboard)
        end
      end),
    }, pane)
  end)

  actions.open_notes = wezterm.action_callback(function(window, pane)
    -- Looking the file up can rename or merge files, so it takes the guard.
    local notes_file = exclusive(window, M.notes_file, config, pane)
    if not notes_file then
      return
    end
    if not file_exists(notes_file) then
      toast(window, 'No notes yet for this tab')
      return
    end
    if config.open_in == 'app' then
      wezterm.open_with(notes_file)
      return
    end
    local args = {}
    for i, a in ipairs(config.split_command) do
      args[i] = a
    end
    args[#args + 1] = notes_file
    pane:split { direction = 'Right', args = args }
  end)

  local function undo(window, pane, notes_file)
    local f = io.open(notes_file, 'r')
    local remaining = f and M.without_last_note(f:read('a'))
    if f then
      f:close()
    end
    if not remaining then
      toast(window, 'No notes to undo')
      return
    end
    if remaining:find('%S') then
      if not rewrite(notes_file, remaining) then
        toast(window, 'Could not update ' .. notes_file)
        return
      end
    elseif not os.remove(notes_file) then
      toast(window, 'Could not remove ' .. notes_file)
      return
    end
    -- Let the same clipboard text be saved again after undoing it. With a
    -- shared file the removed note may be another pane's, so forget every
    -- pane's (at worst, a pane may save the same clipboard text once more).
    if config.file_name:find('{id}', 1, true) then
      last_clipboard[pane_id(pane)] = nil
    else
      for id in pairs(last_clipboard) do
        last_clipboard[id] = nil
      end
    end
    toast(window, 'Removed last note')
  end

  actions.undo = wezterm.action_callback(function(window, pane)
    exclusive(window, function()
      undo(window, pane, M.notes_file(config, pane))
    end)
  end)

  return actions
end

function M.apply_to_config(config, opts)
  config.keys = config.keys or {}
  -- Saving, the clipboard fallback and the default viewer rely on POSIX
  -- tools (mkdir, cp, pbpaste/wl-paste/xclip, less).
  if (wezterm.target_triple or ''):find('windows') then
    wezterm.log_error('term-notes: Windows is not supported yet; no shortcuts were added')
    return config
  end
  local settings = M.settings(opts)
  local actions = M.actions(settings)
  local by_key, order = {}, {}
  for name, spec in pairs(settings.keys) do
    if spec and actions[name] then
      local binding = M.parse_key(spec)
      local id = binding.mods .. '+' .. binding.key
      if not by_key[id] then
        by_key[id] = {}
        order[#order + 1] = id
      end
      table.insert(by_key[id], { name = name, spec = spec, binding = binding })
    end
  end
  table.sort(order)
  for _, id in ipairs(order) do
    local uses = by_key[id]
    if #uses > 1 then
      -- Don't leave precedence to WezTerm: disable all of them.
      local names = {}
      for i, use in ipairs(uses) do
        names[i] = use.name .. ' (' .. use.spec .. ')'
      end
      table.sort(names)
      wezterm.log_error('term-notes: ' .. table.concat(names, ' and ')
        .. ' use the same shortcut, so they are disabled')
    else
      uses[1].binding.action = actions[uses[1].name]
      table.insert(config.keys, uses[1].binding)
    end
  end
  return config
end

return M
