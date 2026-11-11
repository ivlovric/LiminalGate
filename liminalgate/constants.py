"""Channel names shared by the OSC input, the synthetic source, and profiles."""

# Forehead and ear electrodes used by the 4-channel headsets Mind Monitor streams.
EEG_CHANNELS: tuple[str, ...] = ("TP9", "AF7", "AF8", "TP10")
AUX_CHANNELS: tuple[str, ...] = ("AUX1", "AUX2", "AUX3", "AUX4")

# Horseshoe fit codes from the Mind Monitor OSC spec: 1 good, 2 medium, 4 bad.
# A 3 is treated as poor if a device sends it anyway.
HORSESHOE_GOOD = 1
HORSESHOE_BAD = 4
