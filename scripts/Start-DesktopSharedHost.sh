#!/bin/sh
set -eu

usage() {
    printf '%s\n' 'Usage: Start-DesktopSharedHost.sh [--desktop-app APP_BUNDLE] [--host-adapter EXECUTABLE]'
}

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

script_dir=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd -P)
desktop_app=/Applications/ChatGPT.app
adapter=$script_dir/plugins/terminalhook/bin/terminalhook-desktop-host
while [ "$#" -gt 0 ]; do
    case $1 in
        --desktop-app|--host-adapter)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail "Missing path for $1."
            if [ "$1" = --desktop-app ]; then
                desktop_app=$2
            else
                adapter=$2
            fi
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

[ "$(uname -s)" = Darwin ] || fail 'This launcher requires macOS.'
[ -d "$desktop_app" ] || fail "Codex Desktop bundle not found: $desktop_app"
desktop_app=$(CDPATH= cd -P -- "$desktop_app" && pwd -P)
desktop_executable=$desktop_app/Contents/MacOS/ChatGPT
native_cli=$desktop_app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
[ -f "$desktop_executable" ] && [ -x "$desktop_executable" ] || fail "Desktop executable not found: $desktop_executable"
[ -f "$native_cli" ] && [ -x "$native_cli" ] || fail "Desktop native Codex CLI not found: $native_cli"
[ -f "$adapter" ] && [ -x "$adapter" ] || fail "TerminalHook host adapter is missing or cannot be executed: $adapter"
adapter_dir=$(CDPATH= cd -P -- "$(dirname -- "$adapter")" && pwd -P)
adapter=$adapter_dir/$(basename -- "$adapter")

processes=$(ps -axo comm=)
if printf '%s\n' "$processes" | DESKTOP_EXECUTABLE="$desktop_executable" awk '
    { sub(/^[[:space:]]+/, ""); if ($0 == ENVIRON["DESKTOP_EXECUTABLE"]) running = 1 }
    END { exit !running }
'; then
    fail 'Quit Codex Desktop before running this launcher. The current app was left running.'
fi

# Launch Services passes these values to the new app without changing the login
# environment. The adapter gives the real core and the plugin the same socket.
set -- -a "$desktop_app" --env "CODEX_CLI_PATH=$adapter" --env "TERMINALHOOK_DESKTOP_CODEX_BIN=$native_cli"
if [ -n "${CODEX_HOME:-}" ]; then
    set -- "$@" --env "CODEX_HOME=$CODEX_HOME"
fi
if [ -n "${TERMINALHOOK_DESKTOP_SOCKET:-}" ]; then
    set -- "$@" --env "TERMINALHOOK_DESKTOP_SOCKET=$TERMINALHOOK_DESKTOP_SOCKET"
fi
exec open "$@"
