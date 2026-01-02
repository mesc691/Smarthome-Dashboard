# 🏠 Smart Home Dashboard

An elegant wall-mounted dashboard for Raspberry Pi that combines Netatmo weather data, SolarEdge PV production, and astronomical data on a 7-inch display.

![Dashboard Screenshot](screenshot.png)

## ✨ Features

- **Netatmo Integration**: Temperature, humidity, CO₂, air pressure with 72h barograph
- **SolarEdge PV Monitoring**: Live power, daily graph, statistics
- **Astronomy**: Sunrise/sunset, moonrise/moonset, day length, animated sun/moon position
- **Optimized for Wall Mounting**: 7-inch display, readable from 2-3 meters distance
- **Robust**: Offline caching, smart API rate limiting, automatic reconnection

## 🖥️ Hardware

- Raspberry Pi 3/4/5 (or Zero 2 W)
- 7-inch display (800x480 recommended)
- Optional: Case for wall mounting

## 📋 Requirements

- Python 3.9+
- Netatmo Weather Station
- Optional: SolarEdge inverter with API access

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/mesc691/Smarthome-Dashboard.git
cd Smarthome-Dashboard
```

### 2. Install dependencies

```bash
# System packages
sudo apt update
sudo apt install python3-tk python3-pip

# Python packages
pip3 install requests python-dotenv skyfield
```

### 3. Configuration

```bash
# Copy example configuration
cp env.example .env

# Edit configuration
nano .env
```

Fill in all values in `.env` (see comments in the file).

### 4. Netatmo Authentication

On first launch, a browser window opens for Netatmo authentication:

```bash
python3 netatmo_dashboard.py
```

After successful authentication, a token is saved and automatically renewed.

### 5. Set up autostart (optional)

```bash
# Create systemd service
sudo nano /etc/systemd/system/dashboard.service
```

```ini
[Unit]
Description=Smart Home Dashboard
After=graphical.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
WorkingDirectory=/home/pi/Smarthome-Dashboard
ExecStart=/usr/bin/python3 netatmo_dashboard.py
Restart=always
RestartSec=10

[Install]
WantedBy=graphical.target
```

```bash
sudo systemctl enable dashboard
sudo systemctl start dashboard
```

## ⚙️ Configuration

All settings are controlled via the `.env` file:

| Variable | Description | Required |
|----------|-------------|----------|
| `LOCATION_LAT` | Latitude (e.g., 47.3769) | Yes |
| `LOCATION_LON` | Longitude (e.g., 8.5417) | Yes |
| `CLIENT_ID` | Netatmo Client ID | Yes |
| `CLIENT_SECRET` | Netatmo Client Secret | Yes |
| `REDIRECT_URI` | OAuth Redirect URI | Yes |
| `SOLAREDGE_SITE_ID` | SolarEdge Site ID | No |
| `SOLAREDGE_API_KEY` | SolarEdge API Key | No |
| `CONTACT_EMAIL` | Contact for API User-Agent | No |

## 📁 Files

The dashboard creates the following files in the working directory:

- `access_token.json` - Netatmo OAuth token (automatically renewed)
- `dashboard_cache.json` - Offline cache for measurements
- `pressure_history_7inch.json` - 72h air pressure history
- `pv_daily_data.json` - PV daily data
- `de421.bsp` - Skyfield ephemeris data (downloaded automatically)
- `archive/` - Local measurement archive (JSONL)
- `dashboard.log` - Log file

## 🔧 Troubleshooting

### Display stays black
```bash
# Check DISPLAY variable
echo $DISPLAY
export DISPLAY=:0
```

### Netatmo token expired
```bash
# Delete token file, re-authenticate
rm access_token.json
python3 netatmo_dashboard.py
```

### SolarEdge shows no data
- Check API key and Site ID in `.env`
- SolarEdge API is limited to 300 requests/day

### Skyfield ephemeris data
```bash
# Download manually if automatic download fails
wget https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de421.bsp
```

## 📊 API Usage

The dashboard is optimized for conservative API usage:

- **Netatmo**: ~288 requests/day (every 5 min)
- **SolarEdge**: ~280 requests/day (intelligently distributed based on sun position)
- **met.no**: ~96 requests/day (every 15 min, for astronomical data only)

## 🛠️ Development

```bash
# Run in development mode (with logs)
python3 netatmo_dashboard.py

# Check syntax
python3 -m py_compile netatmo_dashboard.py
```

## 📜 License

MIT License - see [LICENSE](LICENSE)

## 🙏 Acknowledgments

- [Netatmo](https://dev.netatmo.com/) for the weather API
- [SolarEdge](https://www.solaredge.com/) for the monitoring API
- [met.no](https://api.met.no/) for the Sunrise API
- [Skyfield](https://rhodesmill.org/skyfield/) for precise astronomical calculations

## 📝 Changelog

### v6.0
- Smart PV query distribution based on sun position
- Animated sun/moon position
- 72h barograph with temperature overlay
- Robust offline caching
- Simplified icon design

---

## 💬 Feedback & Contributions

This project started as a personal need and I appreciate any feedback!

**Using the dashboard?**
- ⭐ Give the project a star on GitHub
- 📸 Share a photo of your installation in [Discussions](../../discussions)
- 💡 Suggest new features via [Issues](../../issues)

**Found a bug?**
- 🐛 Create an [Issue](../../issues/new) with:
  - Description of the problem
  - Error message from `dashboard.log`
  - Your hardware (Pi model, display)

**Want to contribute?**
- 🔧 Pull requests are welcome!
- See [CONTRIBUTING.md](CONTRIBUTING.md) for details

**Contact:**
- GitHub Issues for technical questions
- Discussions for general exchange
