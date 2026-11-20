{ pkgs }: {
  deps = [
    pkgs.tigervnc
    pkgs.fluxbox
    pkgs.xfce.xfce4-terminal
    pkgs.chromium
    pkgs.python3
    pkgs.pulseaudio
    pkgs.pcmanfm

    # Desktop appearance and icon themes
    pkgs.tint2
    pkgs.rofi
    pkgs.yaru-theme
    pkgs.hicolor-icon-theme
  ];
}
