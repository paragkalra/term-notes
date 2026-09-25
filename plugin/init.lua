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
  -- Placeholders: {title}, {dir}, {id}. {id} is appended if missing.
  file_name = '{title}_{id}',
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

function M.settings(opts)
  local settings = M.merge(M.defaults, opts)
  if not settings.file_name:find('{id}', 1, true) then
    settings.file_name = settings.file_name .. '_{id}'
  end
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

function M.basename(path)
  return (path or ''):gsub('/+$', ''):match('([^/]*)$') or ''
end

function M.render_name(template, fields)
  return (template:gsub('{(%w+)}', function(key)
    return fields[key] or ''
  end))
end

-- Same as the iTerm2 script's clean_lines: trim trailing padding and outer
-- blank lines, remove shared indentation, keep internal structure.
function M.clean_lines(text)
  local lines = {}
  for line in ((text or '') .. '\n'):gmatch('(.-)\r?\n') do
    lines[#lines + 1] = (line:gsub('%s+$', ''))
  end
  while #lines > 0 and lines[1] == '' do
    table.remove(lines, 1)
  end
  while #lines > 0 and lines[#lines] == '' do
    table.remove(lines)
  end
  local indent
  for _, line in ipairs(lines) do
    if line ~= '' then
      local n = #line:match('^%s*')
      indent = indent and math.min(indent, n) or n
    end
  end
  for i, line in ipairs(lines) do
    lines[i] = line:sub((indent or 0) + 1)
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
    context[#context + 1] = '`' .. opts.git[1] .. '` @ `' .. opts.git[2] .. '`'
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
  return { key = key, mods = table.concat(mods, '|') }
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

-- WezTerm pane ids restart from 0 with WezTerm, so give each pane a random id
-- (kept across config reloads) to keep its notes file separate.
local function pane_key(pane)
  wezterm.GLOBAL.term_notes_ids = wezterm.GLOBAL.term_notes_ids or {}
  local ids = wezterm.GLOBAL.term_notes_ids
  local key = tostring(pane:pane_id())
  if not ids[key] then
    ids[key] = string.format('%08x', math.random(0, 0x7fffffff))
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

local function file_exists(path)
  local f = io.open(path, 'r')
  if f then
    f:close()
    return true
  end
  return false
end

-- The pane's notes file, renamed if the tab title changed.
function M.notes_file(config, pane)
  local id = pane_key(pane)
  local cwd = cwd_of(pane)
  local fields = { title = M.slug(tab_title(pane)), dir = M.slug(M.basename(cwd)), id = id }
  local wanted = config.notes_dir .. '/' .. M.render_name(config.file_name, fields) .. '.md'
  local wildcard = M.render_name(config.file_name, { title = '*', dir = '*', id = id })
  local ok, existing = pcall(wezterm.glob, config.notes_dir .. '/' .. wildcard .. '.md')
  if ok and existing[1] and existing[1] ~= wanted then
    os.rename(existing[1], wanted)
  end
  return wanted
end

local last_clipboard = {}

-- Returns text, from_clipboard.
local function selected_text(window, pane)
  local text = window:get_selection_text_for_pane(pane)
  if text and text:find('%S') then
    return text, false
  end
  -- Apps with their own mouse handling (Claude Code, Codex, ...) copy their
  -- selection to the clipboard instead of making a terminal selection.
  text = read_clipboard()
  if not text or text == last_clipboard[pane_key(pane)] then
    return nil, false
  end
  return text, true
end

local function toast(window, message)
  window:toast_notification('term-notes', message, nil, 3000)
end

-- Appends the note; returns true on success.
local function save(config, window, pane, text, from_clipboard, comment)
  local lines = M.clean_lines(text)
  local cwd = cwd_of(pane)
  local notes_file = M.notes_file(config, pane)
  run { 'mkdir', '-p', config.notes_dir }
  local f = io.open(notes_file, 'a')
  local ok = f and f:write(M.format_note(lines, {
    when = os.date('%Y-%m-%d %H:%M'),
    title = tab_title(pane),
    cwd = cwd,
    home = wezterm.home_dir,
    git = config.git_context and git_context(cwd) or nil,
    comment = comment,
  }))
  ok = f and f:close() and ok
  if not ok then
    toast(window, 'Could not write ' .. notes_file)
    return false
  end
  -- Only now that the note is on disk, so a failed save can be retried.
  if from_clipboard then
    last_clipboard[pane_key(pane)] = text
  end
  wezterm.log_info('term-notes: saved ' .. #lines .. ' line(s) to ' .. notes_file)
  return true
end

function M.actions(config)
  local actions = {}

  actions.save = wezterm.action_callback(function(window, pane)
    local text, from_clipboard = selected_text(window, pane)
    if #M.clean_lines(text) == 0 then
      toast(window, 'Nothing selected')
      return
    end
    save(config, window, pane, text, from_clipboard)
  end)

  actions.save_with_comment = wezterm.action_callback(function(window, pane)
    local text, from_clipboard = selected_text(window, pane)
    if #M.clean_lines(text) == 0 then
      toast(window, 'Nothing selected')
      return
    end
    window:perform_action(act.PromptInputLine {
      description = 'Comment for this note (Enter to save, Esc to cancel)',
      action = wezterm.action_callback(function(w, p, comment)
        if comment ~= nil then
          save(config, w, p, text, from_clipboard, comment)
        end
      end),
    }, pane)
  end)

  actions.open_notes = wezterm.action_callback(function(window, pane)
    local notes_file = M.notes_file(config, pane)
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

  actions.undo = wezterm.action_callback(function(window, pane)
    local notes_file = M.notes_file(config, pane)
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
      -- Write a temp file and rename it over the original, so a failed
      -- write can't truncate the notes that are being kept.
      local tmp = notes_file .. '.tmp'
      local out = io.open(tmp, 'w')
      local ok = out and out:write(remaining)
      ok = out and out:close() and ok
      if not ok or not os.rename(tmp, notes_file) then
        os.remove(tmp)
        toast(window, 'Could not update ' .. notes_file)
        return
      end
    else
      os.remove(notes_file)
    end
    last_clipboard[pane_key(pane)] = nil
    toast(window, 'Removed last note')
  end)

  return actions
end

function M.apply_to_config(config, opts)
  local settings = M.settings(opts)
  local actions = M.actions(settings)
  config.keys = config.keys or {}
  for name, spec in pairs(settings.keys) do
    if spec and actions[name] then
      local binding = M.parse_key(spec)
      binding.action = actions[name]
      table.insert(config.keys, binding)
    end
  end
  return config
end

return M
