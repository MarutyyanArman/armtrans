"""List and validate audio devices. No silent defaults: the user must pick a device."""


class DeviceError(Exception):
    pass


def _sd():
    try:
        import sounddevice as sd
    except OSError as e:  # PortAudio missing
        raise DeviceError(f"sounddevice could not load PortAudio: {e}") from e
    return sd


def list_input_devices():
    """Return [(index, name, host_api, max_input_channels, default_samplerate)]."""
    sd = _sd()
    hostapis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append((i, d["name"], hostapis[d["hostapi"]]["name"],
                        d["max_input_channels"], d["default_samplerate"]))
    return out


def resolve_mic(index: int) -> dict:
    sd = _sd()
    devices = sd.query_devices()
    if index < 0 or index >= len(devices):
        raise DeviceError(f"No audio device with index {index}. Run `python -m armtrans devices`.")
    d = devices[index]
    if d["max_input_channels"] < 1:
        raise DeviceError(f"Device {index} ({d['name']}) has no input channels; it can't be a microphone.")
    return {"index": index, "name": d["name"], "channels": d["max_input_channels"],
            "samplerate": int(d["default_samplerate"])}
