# EWPE Local

[![CI](https://github.com/mysteriousdev777/ha-ewpe-local/actions/workflows/ci.yml/badge.svg)](https://github.com/mysteriousdev777/ha-ewpe-local/actions/workflows/ci.yml)

Local control of **Gree-platform dehumidifiers** in Home Assistant, such as
**Cooper&Hunter** ones: the dehumidifiers you set up with the **EWPE Smart** or **Gree+**
app. No cloud account: Home Assistant talks to the dehumidifier on your local network.

Check the app first: Cooper&Hunter also sells dehumidifiers on other platforms
(Cooper&Hunter Remote or NetHome Plus app: Midea; Smart Life: Tuya), and those are not
for this integration.

## Highlights

- **A real dehumidifier in Home Assistant**: on and off, target humidity 30-80 %, modes
  Dehumidify, Purify and Dry, from dashboards, automations and voice assistants.
- **Only what the device has**: entities follow the model's panel in the EWPE Smart app,
  16 for the 13805, none left over from air conditioners.
- **Follows the device's panel**: a setting the device would ignore in its current mode is
  refused with a clear message, and the state is read back 2 seconds after every command.
- **Proper sensors**: humidity and temperature with units and long-term statistics, PM2.5
  and odor levels, water tank and filter as problem sensors.
- **Local and careful**: no cloud account, the device key is not stored, the address
  changes only when you change it, diagnostics leave out the address and MAC.
- **Easy to add**: finds the dehumidifier on your network, or takes its IP address; the
  polling interval is adjustable.
- **Nothing to install**: no Python packages of its own, so no version clash with the
  built-in Gree Climate or any other integration, now or after Home Assistant updates.
- **Light on Home Assistant**: one request to the device at a time, nothing blocking;
  tested against a fake device for leaked sockets and tasks.
- **Works without internet** together with [EWPE Keepalive](#fully-local).

## Why not the built-in Gree or ewpe-smart-ha

Both are made for Gree **air conditioners** and handle a dehumidifier as one:
- **Gree Climate**, built into Home Assistant, has only air-conditioner entities
  (`climate`, `switch`): no target humidity, no dehumidifier modes, no humidity or tank
  sensors.
- **[ewpe-smart-ha](https://github.com/anaryk/ewpe-smart-ha)** (0.4.0) shows the
  dehumidifier as an air conditioner, with Cool, Heat and Auto modes and a target
  temperature, plus dozens of air-conditioner entities to hide. Target humidity and the
  dehumidifier mode cannot be set from Home Assistant: they are raw diagnostic numbers, as
  is the humidity itself, without a unit. When the device does not answer, it moves the
  entry to whichever address a device with the same MAC answers a broadcast from.

For Gree air conditioners, use those: this integration covers dehumidifiers only.

## Supported models

Made for the **Cooper&Hunter CH-D014WD6-30LD WF** (MID 13805; Gree module GRJWB04-J,
U-WB05RT, firmware V1.45) and tested with it.

| MID | Panel | Tested |
|---|---|---|
| 13805 | modes Dehumidify, Purify, Dry (laundry); fan Auto, Low, Medium, High; PM2.5 and odor levels | yes, CH-D014WD6-30LD WF |
| 13806, variant `71000001` or `7b000001` | the 13805 panel plus an ambient light | no: from its panel in the EWPE Smart app |

Other Gree dehumidifiers (MID 13800, 13802-13804, other 13806 variants) have other modes
and fan speeds and are not supported: the setup dialog stops and names the model's MID and
variant.

## Entities

- **Dehumidifier**: on and off, target humidity 30-80 % in 5 % steps, mode, current humidity.
- **Fan speed**: Auto, Low, Medium, High.
- Sensors: humidity, temperature, PM2.5 level (Excellent, Good, Bad), odor level (Normal,
  Slight, Serious), error code; Wi-Fi signal (off by default).
- Binary sensors: water tank full (also with the tank removed), filter cleaning (every
  250 hours), with a **Reset filter reminder** button.
- Switches: swing, Health (UV + Ion), light, mute, child lock; ambient light on 13806.

The device's panel rules hold here too: Dry and Purify have no target humidity, Dry keeps
the fan at High, Purify keeps Health on. Home Assistant refuses such a change with an
error instead of sending what the device would ignore. Two seconds after every command
the state is read back: the device may ignore a value or change others itself (Dry turns
the fan to High).

Good to know:
- The device beeps at every command unless **Mute** is on; mute also silences its tank-full
  alarm, so watch the tank in Home Assistant.
- From the network, Health turns on both UV and ion; choosing one is only on the panel.
- The panel's timer is not visible on the network: use automations.
- Error code: 0 means no error; a full tank counts as an error too.

## Fully local

The Gree module answers on the local network only while it reaches its cloud: blocked
from the internet, newer modules stop answering locally, and the device stays
unavailable. **[EWPE Keepalive](https://github.com/mysteriousdev777/ha-ewpe-keepalive)**,
a Home Assistant app, stands in for the Gree cloud on your network, so the device keeps
working with its internet access blocked. This integration does not need it: without it,
leave the device its internet access.

## Install

1. Set the device up on Wi-Fi with the EWPE Smart app and give it a fixed address in your
   router. Home Assistant must reach it on UDP port 7000.
2. In HACS, add `https://github.com/mysteriousdev777/ha-ewpe-local` as a custom repository
   (type Integration), install **EWPE Local** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → EWPE Local**. Leave the address
   empty to search your network and pick the device, or enter its IP address (the search
   does not reach other subnets). A new address later: **Reconfigure** on the integration.

Home Assistant 2025.3 or newer. The integration installs no Python packages.

## How it works

The integration speaks the Gree LAN protocol itself, with the `cryptography` package
Home Assistant already has. Every 30 seconds it asks the device for its status on UDP
port 7000 (encrypted with the device key from a bind at start; the entry keeps only the
address, MAC, model and cipher version). Requests go one at a time; a status with no
reply in 5 seconds is asked again before the device counts as unavailable.

The interval is set in **Configure** on the integration, from 15 to 300 seconds (the
module was tested for hours at 15). To poll from an automation instead, turn polling off
in the integration's **System options** and call `homeassistant.update_entity`.

The search sends one broadcast scan while you add the device. After that the integration
talks only to the address in the entry and never changes it by itself: the protocol does
not authenticate devices, so any device on the network could answer for another.

**Download diagnostics** on the device shows the model, firmware and raw status, without
the address and MAC.

## Development

```
pip install -r requirements-dev.txt         # Python 3.14
pytest                                      # tests
ruff format .                               # format
ruff check .                                # lint
python tools/privacy_check.py --history     # before every push
```

The integration is in `custom_components/ewpe_local/`: `protocol.py` (status keys and
value codes), `models.py` (the model table), `packets.py` (the protocol's packets and
encryption), `client.py` (the device's socket: bind, request and reply, scan),
`coordinator.py` (polling, commands, read-back), one file per platform, `config_flow.py`,
`diagnostics.py`. The tests run it against a fake Gree module on a local UDP port, and
check every packet against [greeclimate](https://github.com/cmroche/greeclimate), an
independent implementation of the protocol (a test dependency only). CI runs them on the
current Home Assistant and on the minimum one (2025.3.0, Python 3.13), with `hassfest`
and the HACS check.

**Privacy.** `tools/privacy_check.py` fails on private addresses and MAC addresses in the
files; list your own MACs, network and user names in `.privacy-local` (ignored by git).

## License

[MIT](LICENSE). Not affiliated with Gree, EWPE or any device maker.
