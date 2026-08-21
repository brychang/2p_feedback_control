target_power = float(input("Enter target power (mW): "))
initial_tolerance = 0.025*target_power
feedback_tolerance = float(input("Enter tolerance (mW): "))
sample_seconds = 5.0
degrees_to_move = float(input("Enter step size (deg) - usually 0.1: "))
testing_state = bool(input("Is this a testing state? (True/False): "))

print(f"Target power: {target_power} mW")
print(f"Initial tolerance: {initial_tolerance} mW")
print(f"Feedback tolerance: {feedback_tolerance} mW")
print(f"Sample seconds: {sample_seconds}")
print(f"Degrees to move: {degrees_to_move}")
print(f"Testing state: {testing_state}")