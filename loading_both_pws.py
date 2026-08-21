import sys

print(sys.version)
print(sys.version_info)

import time
import clr
import os
import msvcrt
import u3

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

    preMEMS.setWavelength(c_double(850.0), 1)
    postETL.setWavelength(c_double(635.0), 1)

    pre_power = c_double()
    post_power = c_double()

    preMEMS.measPower(byref(pre_power), 1)
    postETL.measPower(byref(post_power), 1)

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

        #Define a jog step
        jog_params = controller.GetJogParams()
        jog_params.StepSize = Decimal(degrees_to_move)
        jog_params.MaxVelocity = Decimal(0.5)
        jog_params.JogMode = JogParametersBase.JogModes.SingleStep

        controller.SetJogParams(jog_params)

        #Measure initial power
        current_power = c_double()
        meter.measPower(byref(current_power), 1)

        #And initial position of motor
        print(f'{controller.Position}')

if __name__ == "__main__":
    main()