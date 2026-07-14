# hub/ — host-side hub *(roadmap M3)*

A single container that manages an attached SAM9X60 end-to-end. Placeholder —
implementation lands in milestone **M3**.

Planned (for a Synology NAS via Container Manager, or any Docker host):

- **Connection** — detect the board on the USB-gadget interface, confirm its IP.
- **OTA** — store `.swu` images, pick a version, push it to the board's SWUpdate,
  watch progress and rollback state.
- **Telemetry** — an MQTT broker that ingests the board's sensor stream.
- **Files** — an rsync endpoint syncing the board's `/data` to a host folder.
- **Config** — push settings/commands to the board.
- **UI** — one page: device health, version, telemetry, one-click update.

On a laptop host you don't need this — use the board's own web UI plus a small
CLI instead.
