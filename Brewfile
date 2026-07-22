# Homebrew packages for macOS. Installed/updated by gitclones.sh via
#
#     brew bundle --file="$HOME/Documents/dotfiles/Brewfile"
#
# Python CLI tools are NOT listed here; they are managed with `uv tool` in
# gitclones.sh. Python libraries stay in conda (see gitclones.sh).

tap "d12frosted/emacs-plus"

# Emacs. emacs-plus builds from source (no bottles), so installs/upgrades
# occasionally take a while. It provides both Emacs.app (symlinked into
# /Applications by gitclones.sh) and the terminal emacs/emacsclient binaries
# on PATH. Native compilation is enabled by default in emacs-plus@30.
brew "d12frosted/emacs-plus/emacs-plus@30"

# Shell. Note: the terminal profile should point at /opt/homebrew/bin/bash.
brew "bash"

# CLI tools (moved here from pixi global)
brew "bat"
brew "bottom"      # installs `btm`
brew "duf"
brew "dust"
brew "eslint"
brew "fd"
brew "gh"          # replaces the deprecated `hub`
brew "git-delta"   # installs `delta`
brew "go"
brew "gtop"
brew "moor"        # renamed upstream from moar; still installs a `moar` alias
brew "procs"
brew "ripgrep"
brew "sd"

# Emacs support tools. hunspell ships no dictionaries; the SCOWL en_US
# dictionary is downloaded to ~/.local/share/hunspell by gitclones.sh.
brew "hunspell"
brew "node"        # for copilot.el
brew "ruff"

# Manages the Python CLI tools (see gitclones.sh)
brew "uv"
