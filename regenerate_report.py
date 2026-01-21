import json
import os
import sys
from datetime import datetime

# Import the generation function from the main script
# We can't easily import 'ping_monitor' because it has a main() call at the bottom 
# and potentially asyncio loop stuff that might conflict if not careful.
# But since we need the exact same logic, let's just copy/adapt a small helper or 
# try to import it if it's safe. 
# Looking at the file, 'if __name__ == "__main__": main()' is present, so import is safe.

try:
    import ping_monitor
except ImportError:
    # If it fails (e.g. path issues), we might need to add current dir to sys.path
    sys.path.append(os.getcwd())
    import ping_monitor

def regenerate():
    filename = "downtime_events.json"
    if not os.path.exists(filename):
        print(f"Arquivo {filename} não encontrado.")
        return

    try:
        with open(filename, "r", encoding="utf-8") as f:
            events = json.load(f)
        
        print(f"Lendo {len(events)} eventos de {filename}...")
        ping_monitor.generate_html_report(events)
        print("Relatório 'downtime_report.html' regenerado com sucesso!")
    except Exception as e:
        print(f"Erro ao regenerar: {e}")

if __name__ == "__main__":
    regenerate()
