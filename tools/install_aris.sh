#!/usr/bin/env bash
# install_aris.sh — Project-local ARIS skill installation via symlink.
#
# Recommended over global install for projects that mix ARIS with other skill packs.
# Provides isolation: ARIS skills are only visible inside the attached project.
#
# Usage:
#   bash tools/install_aris.sh [project_path]
#                              [--platform auto|claude|codex]
#                              [--aris-repo PATH]
#                              [--force]
#                              [--no-doc]
#                              [--dry-run]
#
# Defaults:
#   project_path:  current working directory
#   --platform:    auto-detect from project markers (CLAUDE.md / AGENTS.md / .claude/ / .agents/)
#   --aris-repo:   auto-detect (script's parent repo, $ARIS_REPO env var, common paths)
#
# Behavior:
#   - claude platform: symlink <project>/.claude/skills/aris  -> <aris-repo>/skills/
#   - codex platform:  symlink <project>/.agents/skills/aris  -> <aris-repo>/skills/skills-codex/
#   - cursor platform: refused (use docs/CURSOR_ADAPTATION.md instead)
#
# Idempotent: re-running with the same target+source is a no-op (exit 0).
# Existing symlink to a different source: refuse unless --force.
# Updates a managed block in CLAUDE.md or AGENTS.md (between <!-- ARIS:BEGIN --> markers).
# Records install metadata to <project>/.aris/skill-source.txt.

set -euo pipefail

# ─── Parse arguments ───────────────────────────────────────────────────────────
PROJECT_PATH=""
PLATFORM="auto"
ARIS_REPO_OVERRIDE=""
FORCE=false
NO_DOC=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)    PLATFORM="${2:?--platform requires value}"; shift 2 ;;
        --aris-repo)   ARIS_REPO_OVERRIDE="${2:?--aris-repo requires path}"; shift 2 ;;
        --force)       FORCE=true; shift ;;
        --no-doc)      NO_DOC=true; shift ;;
        --dry-run)     DRY_RUN=true; shift ;;
        -h|--help)
            head -28 "$0" | sed 's/^# *//'; exit 0 ;;
        --*)
            echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            if [[ -z "$PROJECT_PATH" ]]; then
                PROJECT_PATH="$1"
            else
                echo "Error: unexpected positional argument: $1" >&2; exit 1
            fi
            shift ;;
    esac
done

# ─── Resolve project path ─────────────────────────────────────────────────────
PROJECT_PATH="${PROJECT_PATH:-$(pwd)}"
if [[ ! -d "$PROJECT_PATH" ]]; then
    echo "Error: project path does not exist: $PROJECT_PATH" >&2; exit 1
fi
PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd)"

# ─── Resolve ARIS repo location ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

resolve_aris_repo() {
    # 1. explicit --aris-repo
    if [[ -n "$ARIS_REPO_OVERRIDE" ]]; then
        if [[ -d "$ARIS_REPO_OVERRIDE/skills" ]]; then
            (cd "$ARIS_REPO_OVERRIDE" && pwd); return 0
        fi
        echo "Error: --aris-repo path has no skills/ subdir: $ARIS_REPO_OVERRIDE" >&2; return 1
    fi
    # 2. script's parent repo
    local parent
    parent="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [[ -d "$parent/skills" ]]; then echo "$parent"; return 0; fi
    # 3. $ARIS_REPO env var
    if [[ -n "${ARIS_REPO:-}" && -d "$ARIS_REPO/skills" ]]; then
        (cd "$ARIS_REPO" && pwd); return 0
    fi
    # 4. common paths
    for p in \
        "$HOME/Desktop/aris_repo" \
        "$HOME/aris_repo" \
        "$HOME/.aris" \
        "$HOME/Desktop/Auto-claude-code-research-in-sleep" \
        "$HOME/.codex/Auto-claude-code-research-in-sleep" \
        "$HOME/.claude/Auto-claude-code-research-in-sleep" ; do
        if [[ -d "$p/skills" ]]; then echo "$p"; return 0; fi
    done
    return 1
}

ARIS_REPO="$(resolve_aris_repo)" || {
    echo "Error: cannot find ARIS repo. Use --aris-repo PATH or set ARIS_REPO env var." >&2; exit 1
}

# ─── Resolve platform ─────────────────────────────────────────────────────────
detect_platform() {
    local has_claude=false has_codex=false has_cursor=false
    [[ -e "$PROJECT_PATH/CLAUDE.md" || -e "$PROJECT_PATH/.claude/skills" || -e "$PROJECT_PATH/.claude/settings.json" ]] && has_claude=true
    [[ -e "$PROJECT_PATH/AGENTS.md" || -e "$PROJECT_PATH/.agents/skills" || -e "$PROJECT_PATH/.codex/config.toml" ]] && has_codex=true
    [[ -e "$PROJECT_PATH/.cursor" || -e "$PROJECT_PATH/.cursor/rules" || -e "$PROJECT_PATH/.cursor/mcp.json" ]] && has_cursor=true

    if $has_cursor && ! $has_claude && ! $has_codex; then echo "cursor"; return; fi
    if $has_claude && $has_codex; then echo "ambiguous"; return; fi
    if $has_claude; then echo "claude"; return; fi
    if $has_codex; then echo "codex"; return; fi
    echo "unknown"
}

if [[ "$PLATFORM" == "auto" ]]; then
    DETECTED="$(detect_platform)"
    case "$DETECTED" in
        claude|codex) PLATFORM="$DETECTED" ;;
        cursor)
            echo "Error: Cursor does not use a project skills directory; see docs/CURSOR_ADAPTATION.md" >&2; exit 1 ;;
        ambiguous)
            echo "Error: project has both Claude and Codex markers; pass --platform claude or --platform codex explicitly" >&2; exit 1 ;;
        unknown)
            echo "Error: cannot auto-detect platform (no CLAUDE.md/AGENTS.md/.claude/.agents found)" >&2
            echo "       Pass --platform claude or --platform codex" >&2; exit 1 ;;
    esac
elif [[ "$PLATFORM" == "cursor" ]]; then
    echo "Error: Cursor does not use a project skills directory; see docs/CURSOR_ADAPTATION.md" >&2; exit 1
elif [[ "$PLATFORM" != "claude" && "$PLATFORM" != "codex" ]]; then
    echo "Error: --platform must be one of: auto, claude, codex (got: $PLATFORM)" >&2; exit 1
fi

# ─── Compute paths ────────────────────────────────────────────────────────────
case "$PLATFORM" in
    claude)
        SOURCE_DIR="$ARIS_REPO/skills"
        TARGET_RELATIVE=".claude/skills/aris"
        DOC_FILE="$PROJECT_PATH/CLAUDE.md"
        ;;
    codex)
        SOURCE_DIR="$ARIS_REPO/skills/skills-codex"
        TARGET_RELATIVE=".agents/skills/aris"
        DOC_FILE="$PROJECT_PATH/AGENTS.md"
        ;;
esac
TARGET_DIR="$PROJECT_PATH/$TARGET_RELATIVE"
TARGET_PARENT="$(dirname "$TARGET_DIR")"

if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "Error: source skill directory does not exist: $SOURCE_DIR" >&2; exit 1
fi

# ─── Print plan ───────────────────────────────────────────────────────────────
echo ""
echo "ARIS Project Install Plan"
echo "  Project:        $PROJECT_PATH"
echo "  Platform:       $PLATFORM"
echo "  ARIS repo:      $ARIS_REPO"
echo "  Source:         $SOURCE_DIR"
echo "  Target:         $TARGET_DIR  (relative: $TARGET_RELATIVE)"
echo "  Doc file:       $DOC_FILE"
$DRY_RUN && echo "  Mode:           DRY-RUN (no changes)" || echo "  Mode:           APPLY"
echo ""

# ─── Idempotency check ────────────────────────────────────────────────────────
if [[ -L "$TARGET_DIR" ]]; then
    EXISTING_TARGET="$(readlink "$TARGET_DIR")"
    if [[ "$EXISTING_TARGET" == "$SOURCE_DIR" ]]; then
        echo "✓ Already installed (symlink points to $SOURCE_DIR)"
        exit 0
    fi
    if ! $FORCE; then
        echo "Error: target exists and points elsewhere:" >&2
        echo "  Target:    $TARGET_DIR" >&2
        echo "  Currently: $EXISTING_TARGET" >&2
        echo "  Expected:  $SOURCE_DIR" >&2
        echo "  Use --force to backup and replace." >&2
        exit 1
    fi
elif [[ -e "$TARGET_DIR" ]]; then
    if ! $FORCE; then
        echo "Error: target exists and is not a symlink: $TARGET_DIR" >&2
        echo "  Use --force to backup (as aris.backup.<timestamp>) and replace." >&2
        exit 1
    fi
fi

# ─── Apply ────────────────────────────────────────────────────────────────────
if $DRY_RUN; then
    echo "(dry-run) would create symlink: $TARGET_DIR -> $SOURCE_DIR"
    $NO_DOC || echo "(dry-run) would update doc:  $DOC_FILE"
    echo "(dry-run) would record metadata: $PROJECT_PATH/.aris/skill-source.txt"
    exit 0
fi

# Backup if force-replacing
if [[ -e "$TARGET_DIR" || -L "$TARGET_DIR" ]] && $FORCE; then
    BACKUP="${TARGET_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
    mv "$TARGET_DIR" "$BACKUP"
    echo "Backed up existing target to: $BACKUP"
fi

# Create parent dir + symlink
mkdir -p "$TARGET_PARENT"
ln -sfn "$SOURCE_DIR" "$TARGET_DIR"
echo "✓ Created symlink: $TARGET_DIR -> $SOURCE_DIR"

# Record metadata
mkdir -p "$PROJECT_PATH/.aris"
ARIS_COMMIT="$(git -C "$ARIS_REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
cat > "$PROJECT_PATH/.aris/skill-source.txt" <<META
platform=$PLATFORM
link_mode=symlink
project_path=$PROJECT_PATH
link_path=$TARGET_DIR
aris_repo=$ARIS_REPO
skill_source=$SOURCE_DIR
aris_commit=$ARIS_COMMIT
installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
META
echo "✓ Recorded metadata: $PROJECT_PATH/.aris/skill-source.txt"

# Update managed block in CLAUDE.md / AGENTS.md
if ! $NO_DOC; then
    BLOCK_BEGIN="<!-- ARIS:BEGIN -->"
    BLOCK_END="<!-- ARIS:END -->"
    BLOCK_BODY="$BLOCK_BEGIN
## ARIS Skill Scope
For ARIS workflows in this project, use only the project-local ARIS skills under \`$TARGET_RELATIVE\`.
Do not use global skills or non-ARIS project skills unless the user explicitly asks to mix them.
$BLOCK_END"

    if [[ -f "$DOC_FILE" ]] && grep -qF "$BLOCK_BEGIN" "$DOC_FILE"; then
        # Replace existing managed block
        python3 - "$DOC_FILE" "$BLOCK_BEGIN" "$BLOCK_END" "$BLOCK_BODY" <<'PYEOF'
import re, sys, pathlib
path, begin, end, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
text = pathlib.Path(path).read_text()
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
new = pattern.sub(body, text)
pathlib.Path(path).write_text(new)
PYEOF
        echo "✓ Updated managed ARIS block in: $DOC_FILE"
    else
        # Append (create file if needed)
        {
            [[ -s "$DOC_FILE" ]] && echo ""
            echo "$BLOCK_BODY"
        } >> "$DOC_FILE"
        echo "✓ Appended managed ARIS block to: $DOC_FILE"
    fi
fi

echo ""
echo "Install complete. Update with:  cd $ARIS_REPO && git pull"
