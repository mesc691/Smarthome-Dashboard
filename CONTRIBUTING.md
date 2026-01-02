# Beitragen zum Smart Home Dashboard

Vielen Dank für dein Interesse, zu diesem Projekt beizutragen! 🎉

## Wie kann ich beitragen?

### 🐛 Bugs melden

1. Prüfe zuerst, ob das Problem bereits als [Issue](../../issues) gemeldet wurde
2. Erstelle ein neues Issue mit:
   - **Titel**: Kurze Beschreibung des Problems
   - **Beschreibung**: Was passiert? Was erwartest du?
   - **Schritte zum Reproduzieren**: Wie kann ich das Problem nachstellen?
   - **Log-Auszug**: Relevante Zeilen aus `dashboard.log`
   - **Umgebung**: Raspberry Pi Modell, Display, Python-Version

### 💡 Features vorschlagen

Hast du eine Idee für eine Verbesserung?

1. Erstelle ein Issue mit dem Label `enhancement`
2. Beschreibe:
   - Was soll das Feature tun?
   - Warum wäre es nützlich?
   - Hast du Ideen zur Umsetzung?

### 🔧 Code beitragen

1. **Fork** das Repository
2. Erstelle einen **Branch** für dein Feature:
   ```bash
   git checkout -b feature/mein-neues-feature
   ```
3. Mache deine Änderungen
4. **Teste** gründlich auf einem Raspberry Pi
5. **Commit** mit aussagekräftiger Nachricht:
   ```bash
   git commit -m "Füge XYZ-Feature hinzu"
   ```
6. **Push** zu deinem Fork:
   ```bash
   git push origin feature/mein-neues-feature
   ```
7. Erstelle einen **Pull Request**

## Code-Stil

- Python 3.9+ kompatibel
- Kommentare auf Deutsch (Code auf Englisch ist auch OK)
- Funktionen dokumentieren mit Docstrings
- Keine hartkodierten Credentials oder Pfade
- Konfiguration über `.env` oder Konstanten am Dateianfang

## Testen

Vor einem Pull Request:

```bash
# Syntax prüfen
python3 -m py_compile netatmo_dashboard.py

# Auf dem Pi testen
python3 netatmo_dashboard.py
```

## Bereiche, wo Hilfe besonders willkommen ist

- 🌍 **Übersetzungen**: README auf Englisch
- 📱 **Andere Displays**: Anpassungen für verschiedene Auflösungen
- 🔌 **Weitere Integrationen**: Andere Wetterstationen, Wechselrichter
- 📊 **Datenexport**: InfluxDB, Home Assistant Integration
- 🎨 **Themes**: Helle Themes, Farbschemata

## Fragen?

Nutze die [Discussions](../../discussions) für Fragen, Ideen und Austausch.

---

Nochmals danke für deine Unterstützung! 🙏
