These are some of my .files

Files in the root of the directory are symbolically linked to
corresponding files in ~/

Note, to symbolically link a file, you must do ``ln -s
~/Documents/dotfiles/.file ~/.file``, i.e., the paths must be
**absolute** paths (use of the ``~`` shortcut is OK).  See
http://superuser.com/questions/302312/how-to-properly-store-dotfiles-in-
a-centralized-git-repository.

Files in other/ are for various other things.