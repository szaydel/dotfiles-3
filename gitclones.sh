#!/usr/bin/env bash
set -e

clone-or-pull () {
    dir=$(python -c "
print('$2' or '$1'.rsplit('.git', 1)[0].split('/')[-1])
")
    if [[ -d $dir ]]
    then
        echo "Pulling $dir"
        cd $dir
        git pull
        cd ..
    else
        echo "Cloning $dir"
        git clone $1 $2
    fi
}

ln -sf ~/anaconda3 ~/anaconda
mkdir -p ~/Documents/gists/
cd ~/Documents/gists/
clone-or-pull git@gist.github.com:3187620.git gist-3187620

cd ~/Documents/
clone-or-pull git@github.com:github/hub.git
# cd hub
# make bin/hub
# cp bin/hub ~/bin/
cd ~/Documents

clone-or-pull git@github.com:git/git.git
cd ~/Documents/git/contrib/diff-highlight/
make
cd ~/Documents

clone-or-pull git@github.com:ipython/ipython.git
# clone-or-pull git://git.sv.gnu.org/emacs.git
clone-or-pull git@github.com:inducer/pudb.git

# Needed in .emacs:
# TODO: byte compile some stuff
# clone-or-pull git://git.sv.gnu.org/auctex.git
# cd auctex
# ./autogen.sh

clone-or-pull git@github.com:dacap/keyfreq.git
clone-or-pull git@github.com:nonsequitur/smex.git
# Until https://github.com/nonsequitur/smex/pull/12 is merged
cd smex
# ignore remote already exists.
git remote add haxney git@github.com:haxney/smex.git || true
git fetch haxney
git checkout customize
git branch --set-upstream-to=haxney/customize customize
cd ~/Documents/

clone-or-pull git@github.com:fgallina/python.el.git
# clone-or-pull git@gitlab.com:python-mode-devs/python-mode.git
cd python.el
# ignore remote already exists
git remote add github git@github.com:asmeurer/python.el.git || true
git fetch github
git checkout indentation
# ignore remote
git branch --set-upstream-to=github/indentation indentation
cd ~/Documents/
clone-or-pull git@github.com:purcell/mmm-mode.git
# TODO: doctest-mode
clone-or-pull git@github.com:tkf/emacs-jedi.git
cd emacs-jedi
echo "Creating conda environment for jedi"
conda install -m -p env jedi epc
cd ~/Documents/
clone-or-pull git@github.com:juergenhoetzel/profile-dotemacs.git

clone-or-pull git@github.com:asmeurer/mypython
mkdir -p ~/bin/
ln -s -f ~/Documents/mypython/bin/mypython ~/bin/mypython

if [[ $(uname) != "Darwin" ]]; then
    clone-or-pull git@github.com:jcs/xbanish.git
    cd xbanish
    make
    cd ~/Documents/
fi

CONDA_PKGS="emacs argcomplete hunspell-en pyflakes mpmath ipython conda-build xonsh hub bash matplotlib pyinstrument pytest sympy pudb setproctitle"
if [[ $(uname) == "Darwin" ]]; then
    CONDA_PKGS="$CONDA_PKGS catimg fortune prefsync"
fi

conda install $CONDA_PKGS

clone-or-pull git@github.com:jwiegley/use-package.git
cd ~/Documents/use-package
make elc
cd ~/Documents

activate-global-python-argcomplete --user

# echo "Running prefsync"
# prefsync ~/Library/Preferences/org.pqrs.Karabiner.plist ~/Documents/dotfiles/Library/Preferences/org.pqrs.Karabiner.plist

if [[ $(uname) == "Darwin" ]]; then
    conda install -m -n fortune fortune
fi
