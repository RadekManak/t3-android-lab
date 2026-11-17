# Onboard an Android device

Add a phone or tablet for general debugging or T3 development. You can perform host setup in a terminal or ask an agent in T3 Code to do it. First-time device settings and sign-in happen on the device until remote access is available.

## Choose availability

Choose the availability preference:

- **Managed:** a device you prefer to keep available. For unattended use, complete device-specific recovery setup and verification before leaving it unattended.
- **On-demand:** a device you connect when needed. It is excluded from automatic host reconnection.

New entries default to on-demand. Both modes use the same pairing and connection commands. On-demand setup ends after connection and screen verification. The mode makes no assumption about ownership or data sensitivity. Registration does not contact the device. Store device names, addresses, modes, and executable paths in the ignored `devices.local.json`.

## Prepare the host and device

Install [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on the Linux host. Use your existing Android Studio SDK, your distribution's package, or Google's standalone download. ADB stays outside this repository and is not bundled with the lab. You do not need Android Studio or a full SDK for device-only debugging.

Put `platform-tools` on `PATH` and check the executable:

```bash
adb version
```

The lab searches `PATH`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `~/Android/Sdk`, and `~/android-sdk`. If your installation is elsewhere, save it with `bin/t3-android-lab device adb /path/to/platform-tools/adb`, or pass `--adb <path>` when registering your first device. The saved path is shared by all entries and takes precedence over discovery. A missing saved executable produces an error rather than silently switching ADB versions. Registration and listing still work before ADB is installed.

1. Install Tailscale on the Linux host and sign in to the intended tailnet. An agent uses T3's `request_secret` for credentials that only you can provide.
2. Ask the user to connect the Android device to Wi-Fi, install Tailscale, and sign in to the same tailnet. The agent can identify its Tailscale address with `tailscale status`. If several entries could match, ask which device to use.
3. Identify the device model and Android version, then look up its manufacturer's instructions for enabling Developer options. Ask the user to follow the matching steps and enter any device unlock code locally. After pairing, confirm the model and OS through device diagnostics.
4. Ask the user to enable **Developer options → Wireless debugging** and allow the current trusted Wi-Fi network. Keep Wi-Fi connected, even though the agent uses the Tailscale address.
5. Register the device without contacting it:

   ```bash
   bin/t3-android-lab device add tablet --host 100.x.x.x
   ```

Use a descriptive local name. To include the device in optional automatic reconnection, substitute `--mode managed`. The inventory uses one host-side ADB executable for all entries. A new inventory does not require sandbox configuration.

Run `bin/t3-android-lab device list` to review the inventory and ADB connection states without connecting to phones. Terminal output is readable text; pass `--json` when an agent or script parses it. Use `bin/t3-android-lab device <action> --help` for examples and options. Failures exit with code 1 and print an error to stderr. Invalid arguments exit with code 2.

Listing does not start the ADB server. It shows `disconnected` when no matching transport exists, and `unknown` when the server cannot be read. It does not test network reachability. Use the registered host spelling for manual ADB connections too; another IP or hostname alias may not match.

## Pair and connect

1. Ask the user to open **Pair device with pairing code** inside Wireless debugging. Get the pairing port from that dialog. Use the device's Tailscale address instead of the LAN address displayed by Android.
2. Call T3's `request_secret` with `delivery: "file"` for the six-digit pairing code. Keep the dialog open until pairing completes. Never request the code in chat, put it in shell history, or save it in the inventory.
3. Pair using the returned secret file:

   ```bash
   bin/t3-android-lab device pair tablet --pairing-port 38063 --code-file /path/returned/by/request_secret
   ```

   When pairing yourself in a terminal, omit `--code-file` to enter the code at a hidden prompt. Do not put it in a command argument. Pairing is per host ADB key. A successful result reports `paired` without printing the code.
4. Close the pairing dialog and keep Wireless debugging enabled. The connection port differs from the pairing port; the lab can discover it automatically.
5. Connect to the named device:

   ```bash
   bin/t3-android-lab device connect tablet
   ```

6. Call T3's `device_list`, then `device_open` with the returned host and device IDs. Use its returned bound AgentDevice command to open Settings and inspect a snapshot. Call `device_screenshot` to verify the screen. Use that command for every launch, deep link, tap, text entry, setting change, screenshot, and recording. ADB remains available for logs, builds, installs, forwards, and diagnostics that AgentDevice does not provide.
7. Close the AgentDevice session you opened when verification ends. Run `device disconnect tablet` when you want to end its ADB connection. You can turn off Wireless debugging and Tailscale afterward. Pairing remains available for a later connection.

On completion, report the local name, mode, whether pairing and screen access were verified, and any remaining manual step. A device that is only paired is not yet verified for control.

If the saved connection port changes, `device connect <name>` discovers and saves its replacement. You can supply the port from the main Wireless debugging screen with `--port <connection-port>`. Add `--no-discover` to use only that port or the saved one. Scanning can transfer several megabytes, so background timers use saved ports only.

## Enable recovery on a managed device

For a managed device that must remain available unattended, follow [Set up an unattended Android device](unattended.md). That procedure requires research for the actual manufacturer, model, and OS, followed by checks on the device and a recovery test. Use it for unattended sandbox devices too. Other managed devices can use it when continuous availability is wanted. On-demand devices need only the pairing and screen verification above.

## Use a device for T3 development

Follow [Set up a machine](guide.md#set-up-a-machine) for the isolated backend. General debugging ends after device verification; it does not import a Codex login or start a dev server.

The sandbox selects one registered device by name in `config.local.json`. Run `sandbox setup --device <name>` with the checkout and login options from the guide, then reserve a worktree with `sandbox prepare`. Its ADB address stays in the device inventory. Use the global lab skill for reservation, Metro, installation, synthetic data, and cleanup. Extra devices remain available through the independent inventory.

## Sources

- [Android wireless ADB pairing](https://developer.android.com/tools/adb#wireless)
- [Tailscale Android installation](https://tailscale.com/docs/install/android)
- [Tailscale always-on VPN](https://tailscale.com/docs/integrations/mdm/google-workspace)
- [KeepADB setup and recovery limits](https://github.com/m00sfett/KeepADB)
