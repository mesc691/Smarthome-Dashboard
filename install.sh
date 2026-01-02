#!/bin/bash
# =============================================================================
# Smart Home Dashboard - Installationsskript
# =============================================================================

set -e

echo "🏠 Smart Home Dashboard - Installation"
echo "======================================="
echo ""

# Prüfe ob wir auf einem Raspberry Pi sind
if [ -f /proc/device-tree/model ]; then
    MODEL=$(cat /proc/device-tree/model)
    echo "✓ Erkannt: $MODEL"
else
    echo "⚠ Kein Raspberry Pi erkannt - Installation trotzdem fortsetzen?"
    read -p "Fortfahren? [j/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "📦 Installiere System-Abhängigkeiten..."
sudo apt update
sudo apt install -y python3-tk python3-pip

echo ""
echo "🐍 Installiere Python-Pakete..."
pip3 install -r requirements.txt

echo ""
echo "⚙️ Konfiguration einrichten..."
if [ ! -f .env ]; then
    cp env.example .env
    echo "✓ .env erstellt aus env.example"
    echo ""
    echo "⚠ WICHTIG: Bearbeite jetzt die .env Datei mit deinen API-Schlüsseln:"
    echo "   nano .env"
    echo ""
else
    echo "✓ .env existiert bereits"
fi

echo ""
echo "📁 Verzeichnisse erstellen..."
mkdir -p archive

echo ""
echo "✅ Installation abgeschlossen!"
echo ""
echo "Nächste Schritte:"
echo "1. Bearbeite .env mit deinen API-Schlüsseln"
echo "2. Starte das Dashboard: python3 netatmo_dashboard.py"
echo "3. Authentifiziere dich bei Netatmo (Browser öffnet sich)"
echo ""
echo "Für Autostart siehe README.md"
