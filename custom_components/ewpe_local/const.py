"""Constants of the EWPE Local integration."""

from typing import Final

DOMAIN: Final = "ewpe_local"

# Config entry data (besides CONF_HOST and CONF_MAC of homeassistant.const)
CONF_MID: Final = "mid"
CONF_VENDER: Final = "vender"
CONF_CIPHER: Final = "cipher"

# Polling interval in seconds, an option (CONF_SCAN_INTERVAL of homeassistant.const).
# The module was tested for 7 hours with a status every 15 seconds: no faster than that.
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 300
# After a command, the state is read back this much later: the device applies some
# changes on its own (a new mode may switch the fan to High), and may ignore a value.
CONFIRM_DELAY: Final = 2.0
