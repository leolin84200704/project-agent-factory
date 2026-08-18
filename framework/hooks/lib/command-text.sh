# Shared by the PreToolUse hooks that inspect a Bash command.
#
# A command string carries two kinds of text: the shell doing things, and data
# it is merely transporting — heredoc bodies holding a script, a commit message,
# a PR body. Guards that grep the whole string treat prose as instructions and
# block work that touches nothing. Three measured cases on 2026-08-18: a regex
# literal naming a DB client, a PR created with `--base main`, and a commit
# message that quoted a force-push command as an example.
#
# Blocked calls are discarded whole, so every one of these also destroyed the
# `git add && git commit` or heredoc chained ahead of it.
#
# Usage: source this file, then
#   SHELL_TEXT=$(command_shell_text "$COMMAND")   # heredoc bodies removed
#   BODY_TEXT=$(command_heredoc_bodies "$COMMAND") # only the bodies
#
# Quotes are written as \047 / \042 so the awk program needs no shell escaping.

_command_split() {  # _command_split <mode:shell|body> <command>
  printf '%s\n' "$2" | awk -v mode="$1" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
    {
      if (delim != "") {
        if (trim($0) == delim) { delim = ""; next }
        if (mode == "body") print
        next
      }
      if (match($0, /<<-?[ \t]*[\047\042]?[A-Za-z_][A-Za-z0-9_]*[\047\042]?/)) {
        d = substr($0, RSTART, RLENGTH)
        sub(/^<<-?[ \t]*/, "", d)
        gsub(/[\047\042]/, "", d)
        delim = d
      }
      if (mode == "shell") print
    }'
}

command_shell_text()     { _command_split shell "$1"; }
command_heredoc_bodies() { _command_split body  "$1"; }
