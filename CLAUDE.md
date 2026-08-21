This is a set of personal configuration files for macOS and Linux. All files
are symlinked to the home directory.

The principle files here are:

- .profile: bash configuration
- .emacs: emacs configuration
- gitclones.sh: script to install some packages
- linkfiles.py: script to symlink all files to ~
- bin/: a few custom scripts that are too complex to put as bash functions in .profile

# Code Style Guidelines
- **Python**:
  - Absolute paths preferred over relative paths
  - Never silence exceptions (prefer full tracebacks)

- **Shell Scripts**:
  - Use `set -e` to exit on errors
  - Include detailed comments describing functionality
  - Prefer absolute paths when possible
  - The bash configuration should be portable for both Mac and Linux. This
    includes using $HOME or ~ to reference the home directory, which is
    different on the different platforms. If something can only work on one
    platform, it should use a conditional so it isn't run on the other.

- **Git Workflow**:
  - Small, focused commits with descriptive messages

# Claude Code Tool Limitations
- **LS Tool**: Does not show hidden files/directories (those starting with
  `.`). Most files in this repository are hidden. You will need to use use `ls
  -la` with the Bash tool instead to see hidden files.
