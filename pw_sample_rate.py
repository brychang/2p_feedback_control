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

    #Measure initial power
    t0 = time.perf_counter()
    preMEMS.measPower(byref(pre_power), 1)
    MEMSpower = pre_power.value * 1000 #mW
    print("pre_power = ", MEMSpower)
    t1 = time.perf_counter()
    print("Time to measure: ", t1-t0)

    t0 = time.perf_counter()
    postETL.measPower(byref(post_power), 1)
    ETLpower = post_power.value * 1000 #mW
    print("post_power = ", ETLpower)
    t1 = time.perf_counter()
    print("Time to measure: ", t1-t0)

if __name__ == "__main__":
    main()