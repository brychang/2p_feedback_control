import sys

print(sys.version)
print(sys.version_info)

import time
import clr
import os
import msvcrt
import u3
from datetime import datetime

# Create filename once when the script starts
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename_stim = f"stim_power_log_{timestamp}.txt"
filename_feedback = f"feedback_power_log_{timestamp}.txt"

# Global flag for stopping the program
stop_flag = [False]

#Point to file path with the Kinesis files for the motor
clr.AddReference('C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll')
clr.AddReference('C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll')
clr.AddReference('C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.KCube.DCServoCLI.dll')

#Import suite of Kinesis libraries for motor control
from Thorlabs.MotionControl.DeviceManagerCLI import * # type: ignore
from Thorlabs.MotionControl.GenericMotorCLI import * # type: ignore
from Thorlabs.MotionControl.GenericMotorCLI import KCubeMotor # type: ignore
from Thorlabs.MotionControl.GenericMotorCLI.ControlParameters import JogParametersBase # type: ignore
from Thorlabs.MotionControl.KCube.DCServoCLI import * # type: ignore
from System import Decimal # type: ignore

#Import suite of libraries for powermeter control
from TLPMX import TLPMX # type: ignore
from ctypes import c_uint32, byref, create_string_buffer, c_bool, c_int, c_double

def main():
    '''The main entry point for the application'''

    """
    Initialize powermeter:
         Have to add the TLPMX.py and TLPMX_32.dll files to the same directory as this script.
         USB plugin to PM100D powermeter necessary.
    """
    os.add_dll_directory(os.getcwd())
    device_count = c_uint32()
    # Temporary object to enumerate devices
    finder = TLPMX()
    finder.findRsrc(byref(device_count))

    if device_count == 0:
        print("No connected powermeter. Check powermeter is on and connected.")
        quit()

    print(f"Found {device_count.value} power meters")
    meters = {}

    for i in range(device_count.value):
        resource = create_string_buffer(1024)
        finder.getRsrcName(i, resource)

        meter = TLPMX()
        meter.open(resource, True, True)

        serial = resource.value.decode().split("::")[3]
        meters[serial] = meter

    postETL = meters["P0005053"]
    preMEMS = meters["P0040956"]

    #Set wavelengths for each
    preMEMS.setWavelength(c_double(850.0), 1)
    postETL.setWavelength(c_double(635.0), 1)

    pre_power = c_double()
    post_power = c_double()

    preMEMS.measPower(byref(pre_power), 1)
    postETL.measPower(byref(post_power), 1)
    #Sanity check
    print(f"Pre MEMS: {pre_power.value:.6e} W")
    print(f"Post ETL: {post_power.value:.6e} W")

    #Serial number of motor controller
    serial_num = str('27273099')

    #Initialize motor 
    DeviceManagerCLI.BuildDeviceList() # type: ignore
    controller = KCubeDCServo.CreateKCubeDCServo(serial_num) # type: ignore

    #Initialize LabJack
    # Open first available U3 device
    d = u3.U3() 

    if not controller == None:
        #Connect controller if one found
        controller.Connect(serial_num)
        print("Connected to device: " + serial_num)
        
        if not controller.IsSettingsInitialized():
            controller.WaitForSettingsInitialized(3000)

        controller.StartPolling(50)
        time.sleep(0.1)
        controller.EnableDevice()
        time.sleep(0.1)

        #Define and load configuration
        config = controller.LoadMotorConfiguration(serial_num, DeviceConfiguration.DeviceSettingsUseOptionType.UseFileSettings) # type: ignore
        config.DeviceSettingsName = str('PRMI-Z8')
        config.UpdateCurrentConfiguration()
        controller.SetSettings(controller.MotorDeviceSettings, True, False) #Finalize settings, True to currently update device, False to not save to file

        #Home the device
        status_bits = controller.GetStatusBits()
        is_homed = (status_bits & 0x00000400) != 0
        if not is_homed:
            print("Device homing...")
            controller.Home(60000)
        else:
            print("Device already homed.")

        # Move to 25 degrees
        target = Decimal(45.0)
        controller.MoveTo(target, 60000)   # 60 s timeout
        print("Moved to 45°, ready to begin")

        target_power = float(input("Enter target power (mW): "))
        initial_tolerance = 0.025*target_power
        #initial_tolerance = 0.3
        feedback_tolerance = float(input("Enter tolerance (mW): "))
        sample_seconds = 4
        degrees_to_move = float(input("Enter step size (deg) - usually 0.1: "))
        testing_state = False
        powermeter = input("Which powermeter are you using? Type 1 for pre-MEMS, 2 for post-ETL: ")

        print("Feedback loop parameters:")
        print(f"Target power: {target_power} mW")
        print(f"Initial tolerance: {initial_tolerance} mW")
        print(f"Feedback tolerance: {feedback_tolerance} mW")
        print(f"Sample seconds: {sample_seconds}")
        print(f"Degrees to move: {degrees_to_move}")
        print(f"Testing state: {testing_state}")

        #Define a jog step
        jog_params = controller.GetJogParams()
        jog_params.StepSize = Decimal(degrees_to_move)
        jog_params.MaxVelocity = Decimal(0.5)
        jog_params.JogMode = JogParametersBase.JogModes.SingleStep

        controller.SetJogParams(jog_params)

        #Measure initial power
        current_power = c_double()
        if powermeter == "2":
            postETL.measPower(byref(current_power), 1)
        elif powermeter == "1":
            preMEMS.measPower(byref(current_power), 1)
        else:
            print("Powermeter not assigned")

        #And initial position of motor
        print(f'{controller.Position}')
        
        #Feedback Loop to get near target power
        steps_in_tolerance = 0
        found_target = False
        print("Finding target power...")
        while found_target == False and not stop_flag[0]:
            # Check if a key was pressed
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'q':  # Press 'q' to stop
                    stop_flag[0] = True
                    print("Stopping program...")
                    break
            
            if powermeter == "2":
                postETL.measPower(byref(current_power), 1)
            elif powermeter == "1":
                preMEMS.measPower(byref(current_power), 1)
            else:
                print("Powermeter not assigned")
            power = current_power.value * 1000  # mW

            while controller.IsDeviceBusy:
                #print("Device is busy, waiting...")
                time.sleep(0.1)
            if power < target_power - initial_tolerance:
                print("Power  = ", power, ", below target, moving.")
                controller.MoveJog(MotorDirection.Forward, 0) # type: ignore
                steps_in_tolerance = 0
            elif power > target_power + initial_tolerance:
                print("Power  = ", power, ", above target, moving.")
                controller.MoveJog(MotorDirection.Backward, 0) # type: ignore
                steps_in_tolerance = 0
            else:
                steps_in_tolerance += 1
                if steps_in_tolerance >= 10: #If we've been in tolerance for 5 steps, consider target found
                    found_target = True
                    print("Target power found.")


        #Feedback Loop Long Term
        print("Entering feedback loop to maintain target power. Press 'q' to stop.")
        while not stop_flag[0]:
            
            # Check if a key was pressed
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'q':  # Press 'q' to stop
                    stop_flag[0] = True
                    print("Stopping program...")
                    break
            
            start_time = time.time()
            power_samples = []
            stim_power = c_double()
            while time.time() - start_time < sample_seconds:
                if powermeter == "2":
                    postETL.measPower(byref(current_power), 1)
                    postETL.measPower(byref(stim_power), 1)
                elif powermeter == "1":
                    preMEMS.measPower(byref(current_power), 1)
                    postETL.measPower(byref(stim_power), 1)
                else:
                    print("Powermeter not assigned")

                stim_power_mw = stim_power.value * 1000 # converted to mW
                with open(filename_stim, "a") as f:
                    f.write(f"{stim_power_mw}\n")
                power = current_power.value * 1000  # mW
                with open(filename_feedback, "a") as f:
                    f.write(f"{power}\n")
                power_samples.append(power)

            avg_power = sum(power_samples) / len(power_samples)

            print(f"avg power over {sample_seconds:.1f}s ={avg_power:.4f} mW")

            while controller.IsDeviceBusy:
                #print("Device is busy, waiting...")
                time.sleep(0.005)

            #Check shutter state
            if testing_state == False:
                voltage = d.getAIN(0) # type: ignore
                if int(voltage) == 0:
                    shutter_open = False
                elif int(voltage) > 0:
                    shutter_open = True
            else:
                shutter_open = False #If testing, assume shutter is open to test functionality without shutter

            if avg_power > (target_power + feedback_tolerance) and not shutter_open:
                print("Power above target, moving backward.")
                controller.MoveJog(MotorDirection.Backward, 0) # type: ignore
            elif avg_power < (target_power - feedback_tolerance) and not shutter_open:
                print("Power below target, moving forward.")
                controller.MoveJog(MotorDirection.Forward, 0) # type: ignore

        controller.StopPolling()
        controller.Disconnect(False)
    preMEMS.close()
    postETL.close()
    d.close()

if __name__ == "__main__":
    main()

