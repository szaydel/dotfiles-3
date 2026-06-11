#!/bin/bash

# Read JSON input from stdin
input=$(cat)

# Extract current directory from JSON
current_dir=$(echo "$input" | jq -r '.workspace.current_dir')

# Extract context window information
used_percentage=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
tokens_total=$(echo "$input" | jq -r '.context_window.context_window_size // 0')

# Extract model display name and reasoning effort level
model_name=$(echo "$input" | jq -r '.model.display_name // empty')
effort_level=$(echo "$input" | jq -r '.effort.level // empty')

# Format token counts (convert to k format if > 1000)
format_tokens() {
    local num=$1
    if [[ $num -ge 1000 ]]; then
        echo "$((num / 1000))k"
    else
        echo "$num"
    fi
}

tokens_total_formatted=$(format_tokens "$tokens_total")

# Use the pre-calculated percentage
tokens_percentage=$(printf '%.0f' "$used_percentage")

# Get current directory basename (equivalent to \W in PS1)
current_basename=$(basename "$current_dir")

# Get conda environment (if available)
conda_env_part=""
if [[ -n "${CONDA_DEFAULT_ENV}" && "${CONDA_DEFAULT_ENV}" != "base" ]]; then
    conda_env_part="${CONDA_DEFAULT_ENV}"
fi

# Get account email from ~/.claude.json
account_email=""
if [[ -f "$HOME/.claude.json" ]]; then
    account_email=$(jq -r '.oauthAccount.emailAddress // empty' "$HOME/.claude.json" 2>/dev/null)
fi

# Get git status (equivalent to __git_ps1)
git_status_part=""
if git rev-parse --git-dir > /dev/null 2>&1; then
    # Check if we're in a git repository
    git_branch=$(git symbolic-ref --short HEAD 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)
    if [[ -n "$git_branch" ]]; then
        # Check for dirty state (equivalent to GIT_PS1_SHOWDIRTYSTATE=1)
        dirty=""
        if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
            dirty="*"
        fi
        
        # Check for untracked files (equivalent to GIT_PS1_SHOWUNTRACKEDFILES=1)
        untracked=""
        if [[ -n $(git ls-files --others --exclude-standard 2>/dev/null) ]]; then
            untracked="%"
        fi
        
        # Check upstream status (equivalent to GIT_PS1_SHOWUPSTREAM="auto")
        upstream=""
        if git rev-parse @{upstream} >/dev/null 2>&1; then
            ahead=$(git rev-list --count @{upstream}..HEAD 2>/dev/null)
            behind=$(git rev-list --count HEAD..@{upstream} 2>/dev/null)
            if [[ "$ahead" -gt 0 && "$behind" -gt 0 ]]; then
                upstream="<>"
            elif [[ "$ahead" -gt 0 ]]; then
                upstream=">"
            elif [[ "$behind" -gt 0 ]]; then
                upstream="<"
            fi
        fi
        
        git_status_part="${git_branch}${dirty}${untracked}${upstream}"
    fi
fi

# Build the status line with colors (using printf for ANSI codes)
# Note: The actual terminal will dim these colors automatically
status_line=""

# Add conda environment part (gray color)
if [[ -n "$conda_env_part" ]]; then
    status_line="${status_line}$(printf '\033[1;38;2;128;128;128m')${conda_env_part}$(printf '\033[0m')"
fi

# Add current directory (white color)
status_line="${status_line}$(printf '\033[1;38;2;255;255;255m')${current_basename}$(printf '\033[0m')"

# Add git status (cyan color)
if [[ -n "$git_status_part" ]]; then
    status_line="${status_line}$(printf '\033[1;38;2;0;255;255m')${git_status_part}$(printf '\033[0m')"
fi

# Add prompt symbol (red color) - removed trailing $ as per instructions
status_line="${status_line}$(printf '\033[1;38;2;255;85;85m')$(printf '\033[0m')"

echo "$status_line"

# Build second line: context info, model + effort, account email
second_line=""
if [[ $tokens_total -gt 0 ]]; then
    context_info="[${tokens_percentage}% of ${tokens_total_formatted}]"
    second_line="$(printf '\033[1;38;2;255;200;100m')${context_info}$(printf '\033[0m')"
fi
if [[ -n "$model_name" ]]; then
    # Pick color by model family
    model_name_lower=$(echo "$model_name" | tr '[:upper:]' '[:lower:]')
    if [[ "$model_name_lower" == *"fable"* ]]; then
        model_color=$(printf '\033[1;38;2;200;130;255m')   # purple
    elif [[ "$model_name_lower" == *"opus"* ]]; then
        model_color=$(printf '\033[1;38;2;255;185;0m')     # gold
    elif [[ "$model_name_lower" == *"haiku"* ]]; then
        model_color=$(printf '\033[1;38;2;100;220;120m')   # green
    else
        model_color=$(printf '\033[1;38;2;150;200;255m')   # blue (sonnet / default)
    fi

    # Pick color by effort level
    effort_color=""
    if [[ -n "$effort_level" ]]; then
        effort_level_lower=$(echo "$effort_level" | tr '[:upper:]' '[:lower:]')
        if [[ "$effort_level_lower" == *"turbo"* || "$effort_level_lower" == *"fast"* ]]; then
            effort_color=$(printf '\033[1;38;2;255;140;0m')    # orange
        elif [[ "$effort_level_lower" == *"extend"* || "$effort_level_lower" == *"high"* ]]; then
            effort_color=$(printf '\033[1;38;2;80;255;80m')    # bright green
        else
            effort_color=$(printf '\033[1;38;2;180;180;180m')  # gray
        fi
    fi

    if [[ -n "$second_line" ]]; then
        second_line="${second_line} "
    fi
    second_line="${second_line}${model_color}${model_name}$(printf '\033[0m')"
    if [[ -n "$effort_level" ]]; then
        second_line="${second_line}${effort_color} (${effort_level})$(printf '\033[0m')"
    fi
fi
if [[ -n "$account_email" ]]; then
    if [[ -n "$second_line" ]]; then
        second_line="${second_line} "
    fi
    second_line="${second_line}$(printf '\033[1;38;2;128;128;128m')${account_email}$(printf '\033[0m')"
fi
if [[ -n "$second_line" ]]; then
    echo "$second_line"
fi