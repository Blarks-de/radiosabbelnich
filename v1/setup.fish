#!/usr/bin/env fish
# Setup für radio_switch.py auf CachyOS (Arch-Basis) mit Fish-Shell.
#
# Aufruf:
#   fish setup.fish

set SCRIPT_DIR (dirname (status --current-filename))
cd $SCRIPT_DIR

echo "==> Installiere System-Abhängigkeiten (ffmpeg, portaudio) via pacman"
sudo pacman -S --needed ffmpeg portaudio

echo "==> Lege venv an ($SCRIPT_DIR/venv)"
python3 -m venv venv

echo "==> Aktiviere venv und installiere Python-Pakete"
source venv/bin/activate.fish
pip install --upgrade pip
pip install numpy sounddevice
deactivate

echo "==> Fertig. Starten mit: fish run.fish"
