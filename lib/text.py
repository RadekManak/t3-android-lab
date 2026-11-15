"""Present CLI results without querying devices or changing their data."""
import time


def fields(value, indent=''):
    lines = []
    for key, item in value.items():
        label = key.replace('_', ' ').capitalize()
        if isinstance(item, dict):
            lines.extend([f'{indent}{label}:', *fields(item, indent + '  ')])
        else:
            if item is None:
                item = 'none'
            elif isinstance(item, bool):
                item = 'yes' if item else 'no'
            elif isinstance(item, list):
                item = ', '.join(map(str, item)) or 'none'
            lines.append(f'{indent}{label}: {item}')
    return lines


def expiry(value):
    until = time.strftime('%H:%M', time.localtime(value['expires_at']))
    return f'until {until} ({value["expires_in_minutes"]} min left)'


def render(key, value):
    if key == 'device list':
        if not value['devices']:
            lines = ['No devices registered.', 'Next: t3-android-lab device add <name> --host <tailscale-ip>']
        else:
            rows = [('NAME', 'MODE', 'HOST', 'SAVED PORT', 'CONNECTION')]
            rows += [(name, d['mode'], d['host'], str(d['port']) if d['port'] else 'port not saved',
                      d['connection']['state'] + (f' ({d["connection"]["serial"]})' if d['connection']['serial'] else ''))
                 for name, d in value['devices'].items()]
            widths = [max(len(row[i]) for row in rows) for i in range(5)]
            lines = ['  '.join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]
        lines += ['', f'ADB: {value["adb"] or "not found"} ({value["adb_source"]})',
                  'Connection status from the local ADB server; devices not contacted.']
        if value['adb_server']['state'] != 'running':
            lines.append('ADB server: ' + value['adb_server']['state'] + '. Listing does not start it.')
        if value['adb_server'].get('detail'):
            lines.append(value['adb_server']['detail'])
        if not value['adb_found']:
            lines.append('Next: install Android SDK Platform-Tools, then run t3-android-lab device adb <path>.')
        return '\n'.join(lines)
    if key == 'status':
        session = value['session']
        reservation = f'{session["owner"]} for {session["worktree"]}, {expiry(session)}' if session else 'none (phone is free)'
        lines = [f'Reservation: {reservation}',
                 f'Sandbox phone: {value["serial"]} (from config.local.json; not probed)',
                 f'Ports: Metro {value["metro_port"]}, backend {value["backend_port"]}',
                 f'Container: {value["container"]}',
                 'Services: ' + ', '.join(f'{name} {state}' for name, state in value['services'].items())]
        if not session:
            lines.append('Next: t3-android-lab prepare <worktree> --owner <thread-id>')
        return '\n'.join(lines)
    if key == 'device add':
        return (f'Registered {value["added"]} ({value["mode"]}) at {value["host"]}; not contacted.\n'
                f'Next: t3-android-lab device pair {value["added"]} --pairing-port <pairing-port>')
    if key == 'device remove':
        return f'Nothing to remove: {value["removed"]}.' if value.get('already_removed') else f'Removed {value["removed"]} from local configuration. Android pairing is unchanged.'
    if key == 'device connect':
        return (f'Connected {value["device"]} at {value["connected"]}; saved port updated.\n'
                'No app port forwards set. Use T3 device_open for UI.')
    if key == 'connect':
        return f'Connected sandbox phone at {value["connected"]}.\nReverse ports: ' + ', '.join(map(str, value['reverse_ports']))
    if key == 'device pair':
        return f'Paired {value["paired"]}.\nNext: t3-android-lab device connect {value["paired"]} --port <connection-port>'
    if key == 'device disconnect':
        return f'Disconnected {value["disconnected"]} from this ADB host.'
    if key == 'release':
        return 'Nothing to release.' if value.get('already_released') else f'Released reservation for {value["owner"]}; Metro stopped.'
    if key == 'extend':
        return f'Reservation for {value["owner"]}: {expiry(value)}.'
    return '\n'.join(fields(value))
