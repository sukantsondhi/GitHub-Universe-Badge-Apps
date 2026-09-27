# Copy to the badge-drive root as secrets.py and fill in your 2.4 GHz Wi-Fi.
# Keep the real file private. Work Status does not need a GitHub API token.
WIFI_SSID = ""
WIFI_PASSWORD = ""

# ----------------------- GitHub Settings ----------------------
# Update with your GitHub username
GITHUB_USERNAME = ""

# Optional: Add your GitHub personal access token for higher API rate limits
# Leave empty for unauthenticated requests (60 requests/hour)
# With token: 5000 requests/hour
# Docs here https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
GITHUB_TOKEN = ""

# ----------------------- Weather Settings ----------------------
# Optional: Override weather location (by default it's auto-detected from IP)
# You can specify location in several ways:
#
# By city name (simple):
# WEATHER_LOCATION = "London"
#
# By city and country (tuple):
WEATHER_LOCATION = ("London", "GB")
#
# By exact coordinates (tuple):
# WEATHER_LOCATION = (51.5074, -0.1278, "London", "GB")
#
# Leave unset or set to None to use auto-detection (default):
# WEATHER_LOCATION = None

# ------------------------ WLED Settings -----------------------
# Optional: WLED device configuration (for WLED controller app)
# WLED_IP = "192.168.1.100"
