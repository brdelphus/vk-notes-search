# USB audio interface drops out under load

## The problem

An external USB audio interface works fine for hours, then clicks, disconnects,
and comes back with a different card index. It happens only while the machine is
busy, which makes it look like a driver bug and sends you into the wrong rabbit
hole.

## The fix

```bash
# check whether USB autosuspend is powering the device down
cat /sys/bus/usb/devices/*/power/control

# disable it for that device
echo on > /sys/bus/usb/devices/1-2/power/control
```

## Why it works

The kernel suspends idle USB devices to save power. An audio interface looks idle
between stream restarts, gets suspended, and the resume can fail under CPU load —
producing a disconnect rather than a glitch. Forcing the power control to `on`
keeps the device enumerated.

## Notes

- The path `1-2` is the USB bus position, not the card number. It survives
  `card index` renumbering, which is why it is the right handle to pin.
- To make it permanent, a udev rule matching the vendor and product ID is the
  durable version of this `echo`.
- If the device re-enumerates with a different index, pin the index by name in the
  audio server config instead of by number.
