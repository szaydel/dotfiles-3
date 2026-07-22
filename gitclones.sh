#!/usr/bin/env bash

# Set up a new machine, or keep an existing one up to date. The script is
# idempotent: it is safe (and intended) to re-run periodically.
#
# System packages come from Homebrew on macOS (see Brewfile) and apt on
# Linux. Python libraries live in the conda base environment; standalone
# Python CLI tools are managed with `uv tool`.
#
# Prerequisites (not installed by this script):
# - Homebrew on macOS (https://brew.sh)
# - Miniconda in ~/miniconda3 (only used for Python libraries)
# - git SSH access to GitHub

set -euo pipefail

# Steps that shouldn't abort the whole run (e.g. one repo failing to pull)
# append to this instead; a summary is printed at the end.
FAILURES=()

clone-or-pull () {
    # clone-or-pull URL [DIR]
    local url=$1
    local dir=${2:-$(basename "${url%.git}")}
    if [[ -d $dir ]]; then
        echo "Pulling $dir"
        git -C "$dir" pull --ff-only || FAILURES+=("git pull $dir")
    else
        echo "Cloning $dir"
        git clone "$url" "$dir" || FAILURES+=("git clone $url")
    fi
}

# ==== System packages ====

if [[ $(uname) == "Darwin" ]]; then
    # conda ships its own `codesign` (from cctools) that shadows
    # /usr/bin/codesign and breaks emacs-plus's post-install signing step
    # ("argument --deep was not expected"). Put the system tools first for
    # all brew commands so they find Apple's codesign, not conda's.
    local_brew() { PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH" brew "$@"; }

    local_brew update
    # Newer brew refuses to load formulas from untrusted third-party taps.
    # (`brew trust` doesn't exist in older brew, hence the || true.)
    local_brew trust d12frosted/emacs-plus 2> /dev/null || true
    # Don't let a single formula failure abort the git-clones / uv / dictionary
    # steps below.
    local_brew bundle --file="$HOME/Documents/dotfiles/Brewfile" \
        || FAILURES+=("brew bundle")
    # Note: this upgrades everything brew manages, not just the Brewfile
    # packages. emacs-plus has no bottles, so an emacs upgrade rebuilds from
    # source and can take a while.
    local_brew upgrade || FAILURES+=("brew upgrade")
    # emacs-plus's `emacs`/`emacsclient` shims run /Applications/Emacs.app if
    # it exists, so the emacs-plus app must live there or terminal emacs will
    # launch some other Emacs.app instead. Symlink it, but never clobber a
    # real (non-symlink) Emacs.app -- if one is in the way, warn instead of
    # silently letting it shadow the new build.
    if [[ ! -e /Applications/Emacs.app || -L /Applications/Emacs.app ]]; then
        ln -sfn "$(brew --prefix)/opt/emacs-plus@30/Emacs.app" /Applications/Emacs.app \
            || FAILURES+=("link Emacs.app into /Applications")
    else
        FAILURES+=("/Applications/Emacs.app is a real directory (not our symlink); it shadows emacs-plus. Remove it and re-run.")
    fi
else
    # Debian/Ubuntu equivalents of the Brewfile.
    sudo apt-get update
    sudo apt-get install -y \
        bash bat fd-find ripgrep \
        hunspell hunspell-en-us emacs nodejs npm golang gh
    # These aren't packaged on all releases; try them individually so one
    # missing package doesn't abort the rest.
    for pkg in duf du-dust sd git-delta procs; do
        sudo apt-get install -y "$pkg" || FAILURES+=("apt install $pkg")
    done
    # Debian installs fd and bat under different names
    mkdir -p ~/bin
    [[ -e ~/bin/fd ]] || ln -s "$(command -v fdfind)" ~/bin/fd
    [[ -e ~/bin/bat ]] || ln -s "$(command -v batcat)" ~/bin/bat
    # uv isn't in apt; use the official installer (installs to ~/.local/bin)
    if ! command -v uv > /dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
fi

# ==== Git clones ====

mkdir -p ~/Documents/gists/
mkdir -p ~/bin/
cd ~/Documents

clone-or-pull git@github.com:git/git.git
(cd ~/Documents/git/contrib/diff-highlight/ && make) \
    || FAILURES+=("make diff-highlight")

clone-or-pull git@github.com:ipython/ipython.git
clone-or-pull git@github.com:inducer/pudb.git

# Emacs packages
clone-or-pull git@github.com:dacap/keyfreq.git

clone-or-pull git@github.com:nonsequitur/smex.git
# Until https://github.com/nonsequitur/smex/pull/12 is merged
(
    cd smex
    # ignore remote already exists
    git remote add haxney git@github.com:haxney/smex.git 2> /dev/null || true
    git fetch haxney
    git checkout customize
    git branch --set-upstream-to=haxney/customize customize
) || FAILURES+=("smex branch setup")

clone-or-pull git@github.com:fgallina/python.el.git
(
    cd python.el
    # ignore remote already exists
    git remote add github git@github.com:asmeurer/python.el.git 2> /dev/null || true
    git fetch github
    git checkout indentation
    git branch --set-upstream-to=github/indentation indentation
) || FAILURES+=("python.el branch setup")

clone-or-pull git@github.com:purcell/mmm-mode.git
clone-or-pull git@github.com:juergenhoetzel/profile-dotemacs.git

clone-or-pull git@github.com:tkf/emacs-jedi.git
# .emacs runs the jedi EPC server with ~/Documents/emacs-jedi/env/bin/python,
# so the packages must be installed into that environment (a plain venv).
(
    cd emacs-jedi
    if [[ ! -d env ]]; then
        echo "Creating environment for emacs-jedi"
        uv venv env
    fi
    uv pip install --python env/bin/python jedi epc
) || FAILURES+=("emacs-jedi env setup")

clone-or-pull git@github.com:jwiegley/use-package.git
(cd use-package && make elc) || FAILURES+=("make use-package")

clone-or-pull git@github.com:asmeurer/mypython
ln -sf ~/Documents/mypython/bin/mypython ~/bin/mypython

if [[ $(uname) != "Darwin" ]]; then
    clone-or-pull git@github.com:jcs/xbanish.git
    (cd xbanish && make) || FAILURES+=("make xbanish")
fi

# ==== Python libraries (conda base) ====

# Python *libraries* stay in conda; standalone CLI tools are managed with uv
# below. esbonio must stay here (importable from the PATH python3) because
# .emacs runs it with "python3 -m esbonio".
CONDA_PKGS="--file=$HOME/Documents/mypython/requirements.txt argcomplete mpmath ipython conda-build matplotlib pytest sympy pudb setproctitle mamba jedi esbonio"
conda install -y -n base $CONDA_PKGS

activate-global-python-argcomplete --user

# ==== Python CLI tools (uv) ====

# Each tool gets its own isolated environment; executables land in
# ~/.local/bin. `uv tool install` is a no-op when already installed.
UV_TOOLS=(
    shell-gpt
    pyflakes              # .emacs flycheck runs the bare pyflakes executable
    pyinstrument
    jedi-language-server
    xonsh
    llm
    glances
    pipdeptree
    pypinfo
    "mcp[cli]"
)
if [[ $(uname) == "Darwin" ]]; then
    # Apple Silicon only
    UV_TOOLS+=(asitop)
fi
for tool in "${UV_TOOLS[@]}"; do
    # A plain install is a no-op once uv owns the tool. On first run it can
    # collide with a leftover pipx/pixi executable of the same name; --force
    # lets uv take it over (it only overrides that guard, not real errors), so
    # retry once with --force before recording a failure.
    uv tool install "$tool" \
        || uv tool install --force "$tool" \
        || FAILURES+=("uv tool install $tool")
done
uv tool upgrade --all || FAILURES+=("uv tool upgrade --all")

# ==== hunspell dictionary ====

# Install a larger English dictionary for hunspell/Emacs.
#
# The brew/apt hunspell packages ship at most the standard en_US dictionary
# (~50k base words), which misses a lot of real English vocabulary. This is a
# SCOWL (Spell Checker Oriented Word Lists) build at size 95 (~150k base
# words), generated by the official tool at http://app.aspell.net/create. It's
# the same upstream the standard dictionary comes from, just much larger, and
# it still rejects ordinary typos.
#
# It is installed outside this repo (in ~/.local/share/hunspell) rather than
# committed here. The basename stays "en_US" so the personal word list
# ~/.hunspell_en_US still auto-loads. .emacs points hunspell at it via an
# absolute -d path; see the ispell-local-dictionary-alist there.
HUNSPELL_DICT_DIR="$HOME/.local/share/hunspell"
if [[ -f "$HUNSPELL_DICT_DIR/en_US.dic" && -f "$HUNSPELL_DICT_DIR/en_US.aff" ]]; then
    echo "SCOWL hunspell dictionary already installed; skipping download"
else
    mkdir -p "$HUNSPELL_DICT_DIR"
    echo "Downloading SCOWL size-95 en_US hunspell dictionary"
    SCOWL_TMP=$(mktemp -d)
    curl -fsSL "http://app.aspell.net/create?max_size=95&spelling=US&max_variant=3&diacritic=both&special=hacker&encoding=utf-8&format=inline&download=hunspell" -o "$SCOWL_TMP/scowl.zip"
    unzip -o -q "$SCOWL_TMP/scowl.zip" -d "$SCOWL_TMP"
    cp "$SCOWL_TMP/en_US-custom.dic" "$HUNSPELL_DICT_DIR/en_US.dic"
    cp "$SCOWL_TMP/en_US-custom.aff" "$HUNSPELL_DICT_DIR/en_US.aff"
    rm -rf "$SCOWL_TMP"
fi

# ==== Summary ====

if (( ${#FAILURES[@]} )); then
    echo
    echo "The following steps failed:" >&2
    printf ' - %s\n' "${FAILURES[@]}" >&2
    exit 1
fi
echo "Done."
