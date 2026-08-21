import u3

# Open first available U3 device
d = u3.U3()

# Read voltage from AIN0
voltage = d.getAIN(0)

print(f"AIN0 Voltage: {voltage:.4f} V")

# Always close the device
d.close()