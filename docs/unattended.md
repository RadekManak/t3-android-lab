# Set up an unattended Android device

Use this procedure for a managed phone or tablet that must remain available without someone nearby, including an unattended T3 sandbox. Complete [pairing and screen verification](onboarding.md#pair-and-connect) first. On-demand devices end setup there and do not need the settings or tests below.

## Identify and research the actual device

Confirm the manufacturer, model, Android version, manufacturer OS version, and build on the device. Use **About phone** and targeted ADB diagnostics such as `getprop ro.product.manufacturer`, `getprop ro.product.model`, `getprop ro.build.version.release`, and `getprop ro.build.display.id`. Also note the installed Tailscale and recovery-app versions.

Research unattended operation for that model and OS before changing recovery settings. Prefer current manufacturer support pages and the apps' official documentation. Match instructions to the installed version and confirm menu names through T3's Device tools. Treat community workarounds as hypotheses to test, and record missing evidence.

Investigate each area below. Record the applicable setting, its observed value, the source, and any remaining dependency. Mark a control as unavailable when the actual device lacks it.

- Background execution: battery optimization, app sleeping, deep sleeping, background data, power saving, autostart, and manufacturer cleanup tools. Check both Tailscale and the recovery app.
- Network recovery: always-on VPN, Wi-Fi reconnection after sleep and reboot, trusted-network approvals, and recovery-app network rules.
- Authorization: ADB authorization expiry, permission removal for unused apps, and Tailscale key expiry on both the Android device and Linux host.
- Restart behavior: scheduled restarts, automatic optimization, update-triggered restarts, first-unlock requirements, and recovery before the user unlocks the device.
- Power: a reliable charger, observed charging state, charging protection, heat, and whether a drained device powers on when power returns.

For Xiaomi, Redmi, or POCO devices, research the installed MIUI or HyperOS version's background autostart and battery restrictions. For Samsung devices, check sleeping-app lists and Device care restart settings. These are research starting points, not settings to assume exist on every model.

## Configure recovery for the intended absence

Use the bound AgentDevice command returned by `device_open` for every device setting change and UI action. ADB can read diagnostic values and grant the recovery app's documented permission. Keep changes limited to the apps and settings needed for unattended access.

1. Enable Tailscale's **Always-on VPN** in Android VPN settings. Allow its background operation using the device-specific controls found above. Verify `settings get secure always_on_vpn_app` reports `com.tailscale.ipn`.
2. Install [KeepADB](https://github.com/m00sfett/KeepADB) from the owner's chosen official distribution, or verify the existing installation. Grant its required permission with the configured ADB executable and current serial:

   ```bash
   adb -s <serial> shell pm grant de.hohnepeople.keepadb android.permission.WRITE_SECURE_SETTINGS
   ```

3. Open KeepADB with the bound AgentDevice command. Enable its keep-alive option, allow background operation, and select the intended trusted Wi-Fi networks. Confirm Android's network approval. Check its installed version's boot and network-recovery behavior against the upstream documentation. Keep recovery restricted to the intended networks.
4. Enable **Disable adb authorization timeout** in Developer options for this unattended device. Verify the switch and read `settings get global adb_allowed_connection_time`. A value of `0` disables expiry. A positive value is milliseconds; `null` means no explicit override, not disabled expiry. If the control is unavailable, report that limitation and research the device's supported alternative before changing a system value.
5. Check unused-app permission removal, sleeping-app lists, autostart, and background restrictions for both apps. Verify KeepADB's `WRITE_SECURE_SETTINGS` permission with package diagnostics and its foreground service with service diagnostics. Use a finite screen timeout and leave **Stay awake while charging** disabled.
6. Inspect automatic and scheduled restarts and update behavior. Agree on a maintenance plan that fits the absence and the tested recovery behavior. Preserve the user's screen lock and update policy unless they authorize changes. Record any mandatory first unlock as a manual recovery dependency.
7. Check Tailscale node-key expiry for both endpoints with `tailscale status --json` and the admin console. If expiry falls within the absence, use the owner's approved expiry policy before handoff. Tailscale's Machines page can disable key expiry per device. Verify the resulting state. Avoid forced reauthentication over the only remote connection.
8. For continuous host reconnection, run `bin/t3-android-lab device install --json`. Check `t3-android-devices.timer` is enabled and active, then run `t3-android-devices.service` once and inspect its result and the device connection. Check user-service startup after host reboot, including lingering where required. The sandbox's `t3-android-lab-connect.timer` only acts during a reservation and does not provide continuous availability by itself.
9. Confirm the phone reports external power and that its charge level is sustainable. Leave the display off during unattended operation. Record any power-recovery behavior that still needs a physical test.

The host reconnect timer uses saved ports only. Android can change its wireless ADB port after a reboot or network change, and mDNS does not cross Tailscale. A named `device connect <name> --json` can discover and save a replacement remotely. This is manual host recovery, not proof that the background timer discovered the new port.

## Test recovery while someone can reach the device

Arrange disruptive tests while the user is beside the device. Reuse existing authorization for a test already approved in the session. Record whether the user intervenes, including unlocks and network approvals.

1. Leave Tailscale and KeepADB in the background and let the display sleep. Check tailnet reachability and run a targeted ADB diagnostic. Then verify screen access through `device_open` and its bound command.
2. With approval, interrupt and restore the intended Wi-Fi connection. Verify Tailscale and Wireless debugging recover. Try the saved connection first; use named port discovery if needed. Record whether recovery required host discovery or a device action.
3. With approval, reboot the phone. Allow a bounded startup grace period, initially up to three minutes, for Wi-Fi, VPN, and KeepADB to start. Use short checks and keep the user informed. A debugging notification can arrive after the lock screen appears. An ADB timeout during shutdown is not proof of lost pairing or a broken host server.
4. Check Tailscale reachability, discover the current ADB port if needed, and run a fresh ADB diagnostic such as `uptime`. Verify pairing works without a new code, timeout remains `0`, and the recovery service is running. Open a fresh Device session for the current serial and verify screen control. Record whether this worked before any local unlock.
5. Restore the intended charging and network state. Close only the AgentDevice sessions you opened. Leave the managed device connected for unattended use.

Report each test as passed, failed, or untested, with the observed recovery path. If the user unlocked the phone or the agent started an app before verification, report that intervention instead of claiming recovery before first unlock. A successful reboot test does not prove recovery after loss of power or a future OS update.

## Diagnose an access failure by layer

Use Tailscale status and ping to distinguish network reachability from ADB availability. If Tailscale works, try the saved port and named ADB discovery. If ADB reports `CERTIFICATE_UNKNOWN`, check the host key's identity and relevant logs before considering new pairing. Preserve the host's ADB key across host maintenance; replacement requires authorization on the phone again.

Key creation time is not the last successful connection time. A certificate rejection and successful pairing afterward establish that trust failed, but do not establish why. Check expiry settings, connection history, authorization revocation, app or OS changes, and host identity before attributing a failure to timeout. KeepADB can restore Wireless debugging; it cannot authorize a new host key.

## Record readiness locally

Store a dated device report under the active workspace's `.scratch/<task>/`, excluded locally from Git. Keep credentials, pairing codes, private keys, and private device details out of committed documentation.

Record the model and OS build, app versions, research links, relevant settings before and after setup, both endpoints' expiry state, host timer status, charging state, and each recovery test's result. Include discovery steps, delays, manual interventions, and unresolved risks. Recheck the report after OS updates, recovery-app changes, or host key replacement.

Call the device ready for the intended absence only when the relevant tests pass and the owner accepts the remaining dependencies. If a test is skipped, state what remains unverified. An online device or running recovery service alone does not establish unattended readiness.

## Sources for device-specific research

- [Android wireless debugging](https://developer.android.com/tools/adb#wireless)
- [Android's default ADB authorization timeout](https://android.googlesource.com/platform/frameworks/base/+/4463f84e63200168b9631b20e7106d3f783113f8/core/java/android/provider/Settings.java)
- [Tailscale unattended operation](https://tailscale.com/docs/how-to/run-unattended) and [key expiry](https://tailscale.com/docs/features/access-control/key-expiry)
- [KeepADB setup and recovery limits](https://github.com/m00sfett/KeepADB)
- [Samsung Device care and automatic restarts](https://www.samsung.com/us/support/answer/ANS10001953/)
- [Xiaomi HyperOS 2 user guide](https://gl123.alsgp0.mi-fds.com/gl123/Generic%20User%20Guide%20for%20HyperOS%202/Generic%20User%20Guide%20for%20HyperOS%202.pdf)
