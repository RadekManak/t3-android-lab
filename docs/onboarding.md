# Onboard an Android device

Add a phone or tablet for general debugging or T3 development. You can perform host setup in a terminal or ask an agent in T3 Code to do it. First-time device settings and sign-in happen on the device until remote access is available.

## Choose availability

Choose the availability preference:

- **Managed:** a device you prefer to keep available. You can configure device-side recovery and optional host reconnection.
- **On-demand:** a device you connect when needed. It is excluded from automatic host reconnection.

New entries default to on-demand. Both modes use the same pairing and connection commands. The mode makes no assumption about ownership or data sensitivity. Registration does not contact the device. Store device names, addresses, modes, and executable paths in the ignored `devices.local.json`.

## Prepare the host and device

Install [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on the Linux host. Use your existing Android Studio SDK, your distribution's package, or Google's standalone download. ADB stays outside this repository and is not bundled with the lab. You do not need Android Studio or a full SDK for device-only debugging.

Put `platform-tools` on `PATH` and check the executable:

```bash
adb version
```

The lab searches `PATH`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `~/Android/Sdk`, and `~/android-sdk`. If your installation is elsewhere, save it with `bin/t3-android-lab device adb /path/to/platform-tools/adb`, or pass `--adb <path>` when registering your first device. The saved path is shared by all entries and takes precedence over discovery. A missing saved executable produces an error rather than silently switching ADB versions. Registration and listing still work before ADB is installed.

1. Install Tailscale on the Linux host and sign in to the intended tailnet. An agent uses T3's `request_secret` for credentials that only you can provide.
2. Ask the user to connect the Android device to Wi-Fi, install Tailscale, and sign in to the same tailnet. The agent can identify its Tailscale address with `tailscale status`. If several entries could match, ask which device to use.
3. Ask the user to enable Developer options. On Samsung, open **Settings → About phone** or **About tablet → Software information** and tap **Build number** seven times. The user enters the device unlock code locally.
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

The working Samsung lab setup uses Android's always-on VPN for Tailscale and [KeepADB](https://github.com/m00sfett/KeepADB) for Wireless debugging recovery. These are separate from host-side ADB reconnection. Apply this section only to a device whose owner selected managed availability.

1. In Android's VPN settings, enable **Always-on VPN** for Tailscale. Exempt Tailscale from battery optimization. Confirm Tailscale remains connected after leaving its app.
2. Install KeepADB from its [official F-Droid entry](https://f-droid.org/packages/de.hohnepeople.keepadb/) or upstream releases. Use the owner's chosen distribution source. Do not bundle its APK in this repository.
3. Grant KeepADB's documented permission once through the paired ADB connection:

   ```bash
   ~/android-sdk/platform-tools/adb -s 100.x.x.x:36000 shell pm grant de.hohnepeople.keepadb android.permission.WRITE_SECURE_SETTINGS
   ```

4. Open KeepADB through the bound AgentDevice command. Enable its **Keep-Alive** option and exempt the app from battery optimization. Select the trusted Wi-Fi networks on which recovery is allowed. Confirm any Android network approval on the device. Use a finite screen timeout and keep **Stay awake while charging** disabled.
5. Verify the current setup through the UI and host diagnostics. `settings get secure always_on_vpn_app` reports `com.tailscale.ipn`; `settings get global adb_wifi_enabled` reports `1`. Package diagnostics show KeepADB's `WRITE_SECURE_SETTINGS` permission granted, and service diagnostics show its foreground service running. These checks inspect current state. They do not prove recovery after reboot.
6. If continuous host reconnection is wanted, run `bin/t3-android-lab device install`. Its timer reconnects managed entries on saved ports only. General device access needs no backend or Metro. If the phone is used only for reserved sandbox sessions, the sandbox's existing reservation timer is sufficient.

KeepADB recovery depends on Android version, Wi-Fi approval, permissions, and the app's network rules. When needed, arrange a reboot or network-change test with the user and verify reconnection afterward. An agent cannot re-enable a disconnected device through that same missing ADB connection.

## Use a device for T3 development

Follow [Set up a machine](../README.md#set-up-a-machine) for the isolated backend. General debugging ends after device verification; it does not import a Codex login or start a dev server.

The sandbox currently selects one device through `config.local.json`. Configure its `serial` with the selected device's Tailscale address and connection port, then reserve a worktree with `prepare`. Use the global lab skill for reservation, Metro, installation, synthetic data, and cleanup. Extra devices remain available through the independent inventory.

## Sources

- [Android wireless ADB pairing](https://developer.android.com/tools/adb#wireless)
- [Tailscale Android installation](https://tailscale.com/docs/install/android)
- [Tailscale always-on VPN](https://tailscale.com/docs/integrations/mdm/google-workspace)
- [KeepADB setup and recovery limits](https://github.com/m00sfett/KeepADB)
