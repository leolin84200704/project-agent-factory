#!/bin/bash
# Generate + install the launchd jobs (dream nightly, weekly Sunday 11:00)
# for THIS machine's checkout path. Labels are derived from the instance
# directory name, so the same script works for every instance unmodified.
#
# History: the old committed plists hardcoded one machine's absolute path, so
# on any other machine the jobs silently never existed — that is how the dream
# pipeline died without anyone noticing. Deriving everything from $AGENT_ROOT
# at install time prevents that class of failure.
#
# Usage: bash scripts/install-launchd.sh          # install/refresh both jobs
#        bash scripts/install-launchd.sh --status # show current state
#        DREAM_HOUR=19 DREAM_MINUTE=0 bash scripts/install-launchd.sh
#          (instances sharing a machine should stagger dream times so they
#           don't compete for the same API quota window)
set -euo pipefail

AGENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTANCE="$(basename "$AGENT_ROOT")"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_DIR" "$AGENT_ROOT/logs"

if [[ "${1:-}" == "--status" ]]; then
    for label in "com.$INSTANCE.dream" "com.$INSTANCE.weekly"; do
        echo "== $label =="
        launchctl list "$label" 2>/dev/null || echo "  not loaded"
    done
    exit 0
fi

make_plist() {
    local label="$1" script="$2" calendar="$3" out="$LAUNCH_DIR/$1.plist"
    cat > "$out" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>$AGENT_ROOT/scripts/$script</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
$calendar
    </dict>
    <key>StandardOutPath</key>
    <string>$AGENT_ROOT/logs/launchd-$label-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$AGENT_ROOT/logs/launchd-$label-stderr.log</string>
    <key>WorkingDirectory</key>
    <string>$AGENT_ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF
    launchctl unload "$out" 2>/dev/null || true
    launchctl load "$out"
    echo "installed + loaded: $out -> $AGENT_ROOT/scripts/$script"
}

DREAM_HOUR="${DREAM_HOUR:-18}"
DREAM_MINUTE="${DREAM_MINUTE:-30}"

make_plist "com.$INSTANCE.dream" run-dream.sh \
"        <key>Hour</key>
        <integer>$DREAM_HOUR</integer>
        <key>Minute</key>
        <integer>$DREAM_MINUTE</integer>"

make_plist "com.$INSTANCE.weekly" weekly-routine.sh \
"        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>11</integer>
        <key>Minute</key>
        <integer>0</integer>"

echo
echo "Verify: launchctl list | grep $INSTANCE"
