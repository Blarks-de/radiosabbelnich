#!/usr/bin/env fish
# Startet radio_switch.py über das venv aus setup.fish.
#
# Aufruf:
#   fish run.fish

set SCRIPT_DIR (dirname (status --current-filename))
cd $SCRIPT_DIR

if not test -d venv
    echo "Kein venv gefunden — erst 'fish setup.fish' ausführen."
    exit 1
end

source venv/bin/activate.fish
python3 radio_switch.py $argv
deactivate
