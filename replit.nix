{ pkgs }: {
  deps = [
    pkgs.tigervnc
    pkgs.fluxbox
    pkgs.xfce.xfce4-terminal
    pkgs.chromium
    pkgs.python3
    pkgs.pcmanfm

    # Lightweight desktop appearance additions
    pkgs.tint2
    pkgs.rofi
    pkgs.yaru-theme
  ];
}
