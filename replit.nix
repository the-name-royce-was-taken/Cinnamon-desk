{ pkgs }: {
  deps = [
    # VNC server and browser-based remote desktop
    pkgs.tigervnc
    pkgs.python3
    pkgs.chromium

    # Cinnamon desktop environment
    pkgs.cinnamon
    pkgs.cinnamon-session
    pkgs.cinnamon-desktop
    pkgs.cinnamon-menus
    pkgs.cinnamon-settings-daemon
    pkgs.cinnamon-control-center
    pkgs.muffin
    pkgs.nemo
    pkgs.xapp

    # Session services and desktop integration
    pkgs.dbus
    pkgs.dconf
    pkgs.gnome-keyring
    pkgs.polkit_gnome
    pkgs.gnome-themes-extra
    pkgs.adwaita-icon-theme
    pkgs.xdg-utils
    pkgs.xdg-user-dirs

    # Keep a lightweight terminal available as a fallback
    pkgs.xfce.xfce4-terminal
  ];
}
