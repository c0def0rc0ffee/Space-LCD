#!/usr/bin/env bash
# <summary>
# Build script for Space LCD: gate, stage, stamp, package both zips, mirror the App
# folder, verify. See 00 Core (All Projects).md section 7.
#
#   ./build-zip.sh              bump the build number, gate, package, mirror
#   ./build-zip.sh --no-bump    same, keeping the current VERSION
#   ./build-zip.sh --skip-tests emergencies only, prints UNVERIFIED BUILD
# </summary>
# <remarks>
# The version bump is written back only after everything has succeeded, so a failed
# build never eats a number. The source keeps __version__ = "dev"; the staged copies
# are stamped with the new number and the build fails if the placeholder has moved.
# Every gate step fails the build on its own (compile, shell syntax, tests, house
# rules): nothing is packaged past a red step. Verification compares the Git zip with
# git ls-files, the App folder with the Dist zip, and greps both zips for the local
# only names from .git/info/exclude, rather than trusting the staging.
# </remarks>
set -euo pipefail

# ----------------------------------------------------------------------------
# STACK SECTION. This is the part to fill in per project.
# ----------------------------------------------------------------------------
PRODUCT="Space LCD"          # names the output folders and the zips
SLUG="space-lcd"                # lowercase, used in zip filenames
KIND="app"                     # "web" for a Web folder, "app" for an App folder
USES_VITE="no"                 # "yes" adds the Vite folder to the chain
APACHE_NAME=""                 # docroot /var/www/html/<name>, empty for none

# The gate. Use whatever this project actually has. Leave the fallback if there
# is no suite yet, but do not leave it empty: something has to be able to fail.
# The venv install.sh makes, outside the synced tree. SPACE_LCD_PY overrides it.
PY="${SPACE_LCD_PY:-$HOME/.local/share/space-lcd/venv/bin/python}"
# <summary>The gate: compile, shell syntax, then the unit tests. Each step fails on its own.</summary>
run_tests() {
    [ -x "$PY" ] || { echo "no venv at $PY: run bash install.sh first" >&2; return 1; }
    "$PY" -m py_compile src/space_lcd/*.py tools/*.py tests/*.py || { echo "py_compile failed" >&2; return 1; }
    bash -n install.sh build-zip.sh || { echo "shell syntax check failed" >&2; return 1; }
    QT_QPA_PLATFORM=offscreen "$PY" -m unittest discover -s tests 2>&1 | grep -v propagateSizeHints
    return "${PIPESTATUS[0]}"
}

# Where the version is displayed in source, as "dev". Stamped in the staged copies only.
VERSION_FILE="src/space_lcd/__init__.py"
VERSION_LINE='__version__ = "dev"'

# Runtime files that must never reach the Dist zip or a mirror, because the
# server owns them. A stale upload of one of these zeroed a live site once.
SERVER_OWNED=( )               # e.g. "data/stats.json" "uploads"

# Files exempted from the house rules gate, each one reviewed. Keep this short.
ALLOW=( )                      # e.g. --allow "api/token.php"
# ----------------------------------------------------------------------------

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

bump=1; skip_tests=0
for arg in "$@"; do
    case "$arg" in
        --no-bump)    bump=0 ;;
        --skip-tests) skip_tests=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 1 ;;
    esac
done

# <summary>Prints a message to stderr and stops the build, before anything is bumped.</summary>
die()  { echo "ERROR: $*" >&2; exit 1; }
# <summary>Prints a message to stderr and carries on; for steps that may be skipped.</summary>
warn() { echo "WARNING: $*" >&2; }

for tool in rsync zip unzip python3; do
    command -v "$tool" >/dev/null || die "$tool not found: sudo apt install $tool"
done
[ "$USES_VITE" = "yes" ] && { command -v npm >/dev/null || die "npm not found"; }

[ -f VERSION ] || die "no VERSION file"
version="$(tr -d '[:space:]' < VERSION)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "VERSION is not major.minor.build: $version"
if [ "$bump" = 1 ]; then
    next="${version%.*}.$(( ${version##*.} + 1 ))"
else
    next="$version"
fi
echo "Building $PRODUCT v$next"

# 1. Gate --------------------------------------------------------------------
if [ "$skip_tests" = 1 ]; then
    warn "UNVERIFIED BUILD: tests skipped"
else
    run_tests || die "tests failed: not packaging"
fi

# 2. Stage -------------------------------------------------------------------
stage="$(mktemp -d)"; src_stage="$(mktemp -d)"
trap 'rm -rf "$stage" "$src_stage"' EXIT

# Local-only exclusions are kept in .git/info/exclude, not here and not in
# .gitignore, because this script and .gitignore both ship in the source zip
# and a published file must not carry those names. Read them at build time so
# the zips match what git ignores. No patterns means the protection is gone,
# so the build stops rather than packaging without it.
local_excludes=()
local_patterns=()
if [ -f "$root/.git/info/exclude" ]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "${line// }" || "$line" == \#* ]] && continue
        local_excludes+=(--exclude="$line")
        local_patterns+=("$line")
    done < "$root/.git/info/exclude"
fi
[[ ${#local_patterns[@]} -gt 0 ]] || die "no local exclude patterns found: refusing to package"

# Fail closed. An exclusion that silently stopped working is the whole risk
# here, so check the staged trees rather than trusting the patterns above.
# <summary>Stops the build if any .git/info/exclude name is found anywhere under the given stage.</summary>
assert_local_excluded() {
    local dir="$1" pat clean
    for pat in "${local_patterns[@]}"; do
        clean="${pat%/}"; clean="${clean#/}"
        if [ -n "$(find "$dir" -name "$clean" -print -quit 2>/dev/null)" ]; then
            die "a locally excluded entry reached the stage ($clean): refusing to package"
        fi
    done
}

# .gitignore is the single exclusion list. Everything below is in addition to it.
exclude=( --exclude='.git/' --exclude='node_modules/'
          --exclude="$PRODUCT Dist/" --exclude="$PRODUCT Git/" --exclude="$PRODUCT Web/"
          --exclude="$PRODUCT App/" --exclude="$PRODUCT Vite/" )
[ -f .gitignore ] && exclude+=( --exclude-from=.gitignore )

# rsync does not understand gitignore's "!" negation, so anything .gitignore
# re-includes has to be named as an --include BEFORE the excludes, first match
# winning. A committed *.example.* template must reach the Git zip because a
# fresh deploy needs it, and must NOT reach the Dist zip, where it is
# housekeeping. So the include goes on the source stage only.
keep=( --include='*.example' --include='*.example.*' )

# The Git zip is the whole source tree.
rsync -a --chmod=D755,F644 "${local_excludes[@]}" "${keep[@]}" "${exclude[@]}" ./ "$src_stage/"
assert_local_excluded "$src_stage"

# <summary>Stamps the version into one staged copy; the grep guard fails if the placeholder moved.</summary>
stamp_version() {
    local dir="$1"
    grep -qF "$VERSION_LINE" "$dir/$VERSION_FILE" || die "version placeholder not found in $VERSION_FILE: refusing to package"
    sed -i "s/^__version__ = \"dev\"$/__version__ = \"$next\"/" "$dir/$VERSION_FILE"
    grep -qF "__version__ = \"$next\"" "$dir/$VERSION_FILE" || die "version stamp failed in $dir"
}
stamp_version "$src_stage"
echo "$next" > "$src_stage/VERSION"

# The Dist zip is runtime files only. The one tool it keeps is the Kraken's udev setup,
# which install.sh points to; rsync's first match wins, so its include comes first.
dist_exclude=( --include='tools/' --include='tools/setup-kraken-udev.sh' --exclude='tools/*'
    "${exclude[@]}"
    --exclude='README.md' --exclude='CHANGELOG.md' --exclude='PLAN.md' --exclude='JOURNAL.md'
    --exclude='VERSION' --exclude='.gitignore'
    --exclude='.gitattributes' --exclude='build-zip.sh' --exclude='*.example.*'
    --exclude='docs/' --exclude='deploy/' --exclude='dev/' --exclude='tests/'
    --exclude='_dev_*' --exclude='_START HERE.md'
    --exclude='new-project.sh' )
for owned in "${SERVER_OWNED[@]}"; do dist_exclude+=( --exclude="$owned" ); done

if [ "$USES_VITE" = "yes" ]; then
    vite_stage="$(mktemp -d)"
    npm run build -- --outDir "$vite_stage" --emptyOutDir || die "vite build failed"
    rm -rf "$PRODUCT Vite"; mkdir -p "$PRODUCT Vite"
    rsync -a --chmod=D755,F644 "$vite_stage/" "$PRODUCT Vite/"
    rm -rf "$vite_stage"
    rsync -a --chmod=D755,F644 "$PRODUCT Vite/" "$stage/"
    assert_local_excluded "$stage"
else
    rsync -a --chmod=D755,F644 "${local_excludes[@]}" "${dist_exclude[@]}" ./ "$stage/"
    assert_local_excluded "$stage"
    stamp_version "$stage"
fi

# 3. House rules gate --------------------------------------------------------
python3 tools/check_house_rules.py "$stage" "${ALLOW[@]}" || die "house rules gate failed"
python3 tools/check_house_rules.py "$src_stage" "${ALLOW[@]}" || die "house rules gate failed"

# 4. Package -----------------------------------------------------------------
mkdir -p "$PRODUCT Dist" "$PRODUCT Git"
dist_zip="$root/$PRODUCT Dist/$SLUG-v$next.zip"
git_zip="$root/$PRODUCT Git/$SLUG-v$next-src.zip"
rm -f "$dist_zip" "$git_zip"
( cd "$stage"     && zip -qr "$dist_zip" . -x '.git/*' )
( cd "$src_stage" && zip -qr "$git_zip"  . -x '.git/*' )

# 5. Mirrors -----------------------------------------------------------------
case "$KIND" in
    web) local_copy="$PRODUCT Web" ;;
    app) local_copy="$PRODUCT App" ;;
    *)   die "KIND must be web or app" ;;
esac
mkdir -p "$local_copy"
rsync -a --delete "$stage/" "$local_copy/"
echo "Mirrored: $local_copy"

if [ -n "$APACHE_NAME" ]; then
    docroot="/var/www/html/$APACHE_NAME"
    if [ -d "$docroot" ] && [ -w "$docroot" ]; then
        protect=(); for owned in "${SERVER_OWNED[@]}"; do protect+=( --exclude="$owned" ); done
        rsync -rlt --delete "${protect[@]}" "$stage/" "$docroot/"
        echo "Mirrored: $docroot"
    else
        warn "$docroot missing or not writable: run deploy/setup-apache.sh with sudo"
    fi
fi

# 6. Verify ------------------------------------------------------------------
echo
for z in "$dist_zip" "$git_zip"; do
    unzip -t "$z" >/dev/null || die "corrupt zip: $z"
    if unzip -l "$z" | grep -qE '\\|\.git/|node_modules/'; then
        die "bad entries in $z: backslashes, .git or node_modules"
    fi
    hits="$(unzip -l "$z" | grep -iE 'cred|secret|passw|token|\.env|\.pem|\.key|sftp' | grep -viE 'example|AuthToken|PasswordSec' || true)"
    [ -n "$hits" ] && { echo "$hits"; die "secret looking entries in $z"; }
    # The local only names again, this time in the finished zip rather than the stage.
    for pat in "${local_patterns[@]}"; do
        clean="${pat%/}"; clean="${clean#/}"; clean="${clean//\*/}"
        if unzip -Z1 "$z" | grep -qiF -- "$clean"; then
            die "a locally excluded name ($clean) is inside $z"
        fi
    done
    echo "Built: $(basename "$z") ($(du -m "$z" | cut -f1) MB, $(unzip -l "$z" | tail -1 | awk '{print $2}') entries)"
done

# The Git zip must list exactly what git tracks (plus the stamped VERSION, which is
# tracked anyway). Untracked files in the zip or tracked files missing from it both fail.
verify_dir="$(mktemp -d)"
trap 'rm -rf "$stage" "$src_stage" "$verify_dir"' EXIT
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files | LC_ALL=C sort > "$verify_dir/tracked"
    unzip -Z1 "$git_zip" | grep -v '/$' | LC_ALL=C sort > "$verify_dir/zipped"
    if ! diff -u "$verify_dir/tracked" "$verify_dir/zipped" > "$verify_dir/git.diff"; then
        cat "$verify_dir/git.diff"
        die "the Git zip does not match git ls-files (untracked files staged, or tracked files missing)"
    fi
    echo "Verified: Git zip matches git ls-files ($(wc -l < "$verify_dir/tracked") files)"
else
    warn "not a git working tree: Git zip not compared with git ls-files"
fi

# The App folder must be the Dist zip, file for file.
mkdir -p "$verify_dir/dist"
unzip -q "$dist_zip" -d "$verify_dir/dist"
if ! diff -r "$verify_dir/dist" "$local_copy" > "$verify_dir/app.diff"; then
    cat "$verify_dir/app.diff"
    die "the $local_copy folder differs from the Dist zip"
fi
echo "Verified: $local_copy matches the Dist zip"

# 7. Persist the bump only now that everything worked ------------------------
if [ "$bump" = 1 ]; then
    echo "$next" > VERSION
    [ -f package.json ] && python3 - "$next" <<'PY'
import json, sys, pathlib
p = pathlib.Path("package.json"); d = json.loads(p.read_text())
d["version"] = sys.argv[1]
p.write_text(json.dumps(d, indent=2) + "\n")
PY
    echo "VERSION now $next"
fi
echo "Done."
