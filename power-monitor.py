import psutil
import os
import subprocess
import time
import json
import threading
import signal
import sys
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor
import paho.mqtt.client as mqtt

# --- CONFIGURATION ---
MQTT_HOST = "192.168.1.110"
MQTT_TOPIC = "proxmox/system_power"
MINT_USER = "ojobinv"
MINT_IP = "192.168.1.237"

THRESHOLDS = {'LOW': 80, 'NORMAL': 160, 'MED': 240, 'HIGH': 320}

COLORS = {
    'LOW': RGBColor(0, 150, 255),        # Blue
    'NORMAL': RGBColor(255, 255, 255), # White
    'MED': RGBColor(255, 255, 0),      # Yellow
    'ORANGE': RGBColor(255, 165, 0),   # Orange
    'HIGH': RGBColor(255, 0, 0)        # Red
}

# Shared state between threads
current_target_color = COLORS['LOW']
current_displayed_color = COLORS['LOW']

def get_cpu_stats():
    # Power
    try:
        out = subprocess.check_output("sensors | grep 'PPT:' | awk '{print $2}'", shell=True, text=True)
        power = int(float(out.strip()))
    except:
        power = 0
        
    # Usage and Temp
    try:
        usage = round(psutil.cpu_percent())
        # 'k10temp' is the standard driver name for Ryzen processors
        temp = round(psutil.sensors_temperatures().get('k10temp', [{}])[0].current)
    except:
        usage = 0
        temp = 0
        
    return power, usage, temp

def get_gpu_stats():
    try:
        # Requesting power, usage, and temp in a single batch command
        cmd = f"ssh -o BatchMode=yes -o ConnectTimeout=2 {MINT_USER}@{MINT_IP} 'nvidia-smi --query-gpu=power.draw,utilization.gpu,temperature.gpu --format=csv,noheader,nounits' 2>/dev/null"
        out = subprocess.check_output(cmd, shell=True, text=True).strip().split(',')
        return int(float(out[0])), int(out[1]), int(out[2])
    except:
        return 0, 0, 0

def get_extra_temps():
    try:
        temps = psutil.sensors_temperatures()
        
        # 1. NVMe SSD
        nvme = round(temps.get('nvme', [{}])[0].current) if 'nvme' in temps else 0
        
        # 2. DDR5 RAM (Finds both sticks and returns the hotter of the two)
        ram_sticks = temps.get('spd5118', [])
        ram = round(max([stick.current for stick in ram_sticks])) if ram_sticks else 0
        
        return nvme, ram
    except:
        return 0, 0

def get_gradient_color(watts):
    stops = [
        (THRESHOLDS['LOW'], COLORS['LOW']),        
        (THRESHOLDS['NORMAL'], COLORS['NORMAL']), 
        (THRESHOLDS['MED'], COLORS['MED']),        
        (THRESHOLDS['HIGH'], COLORS['ORANGE']),   
        (400, COLORS['HIGH'])                      
    ]
    
    if watts <= stops[0][0]: return stops[0][1]
    if watts >= stops[-1][0]: return stops[-1][1]
    
    for i in range(len(stops) - 1):
        w1, c1 = stops[i]
        w2, c2 = stops[i+1]
        if w1 <= watts <= w2:
            t = (watts - w1) / (w2 - w1)
            r = int(c1.red + (c2.red - c1.red) * t)
            g = int(c1.green + (c2.green - c1.green) * t)
            b = int(c1.blue + (c2.blue - c1.blue) * t)
            return RGBColor(r, g, b)

def sensor_polling_thread(mqtt_c):
    """Runs every 2 seconds. Heavy I/O happens here."""
    global current_target_color
    smoothed_total = 0.0
    ALPHA = 0.2

    while True:
        cpu_pwr, cpu_use, cpu_tmp = get_cpu_stats()
        gpu_pwr, gpu_use, gpu_tmp = get_gpu_stats()
        nvme_tmp, ram_tmp = get_extra_temps()
        raw_total = cpu_pwr + gpu_pwr

        # The expanded payload
        payload = json.dumps({
            "cpu_power": cpu_pwr, "cpu_usage": cpu_use, "cpu_temp": cpu_tmp,
            "gpu_power": gpu_pwr, "gpu_usage": gpu_use, "gpu_temp": gpu_tmp,
            "nvme_temp": nvme_tmp, "ram_temp": ram_tmp,
            "total_power": raw_total
        })
        mqtt_c.publish(MQTT_TOPIC, payload)

        if smoothed_total == 0.0:
            smoothed_total = raw_total
        else:
            smoothed_total = (ALPHA * raw_total) + ((1.0 - ALPHA) * smoothed_total)

        current_target_color = get_gradient_color(smoothed_total)
        time.sleep(2)

def animation_thread(mobo, cooler):
    """Runs at 15 FPS. Fast TCP pushes."""
    global current_displayed_color
    
    while True:
        if current_displayed_color != current_target_color:
            r = current_displayed_color.red + (current_target_color.red - current_displayed_color.red) * 0.15
            g = current_displayed_color.green + (current_target_color.green - current_displayed_color.green) * 0.15
            b = current_displayed_color.blue + (current_target_color.blue - current_displayed_color.blue) * 0.15
            
            if abs(current_target_color.red - r) < 2 and abs(current_target_color.green - g) < 2 and abs(current_target_color.blue - b) < 2:
                current_displayed_color = current_target_color
            else:
                current_displayed_color = RGBColor(int(r), int(g), int(b))
            
            mobo.set_color(current_displayed_color)
            cooler.set_color(current_displayed_color)
            
        time.sleep(0.066)

def main():
    print("Checking OpenRGB hardware server...")
    # Clean Python-native check to prevent systemd crash loop
    if os.system('pgrep -x "openrgb" > /dev/null') != 0:
        print("Starting background OpenRGB server...")
        os.system('/usr/local/bin/openrgb --server > /dev/null 2>&1 &')
        time.sleep(5) 

    print("Connecting to OpenRGB Server...")
    try:
        client = OpenRGBClient() 
    except Exception as e:
        print(f"CRITICAL: Could not connect to OpenRGB server. {e}")
        return

    mobo = client.devices[2]
    cooler = client.devices[3]
    ram1 = client.devices[0]
    ram2 = client.devices[1]

    # Initialize hardware
    ram1.set_mode('direct')
    ram2.set_mode('direct')
    ram1.set_color(RGBColor(255, 255, 255))
    ram2.set_color(RGBColor(255, 255, 255))

    mobo.set_mode('direct')
    cooler.set_mode('direct')
    mobo.set_color(COLORS['LOW'])
    cooler.set_color(COLORS['LOW'])

    mqtt_c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    # ADD THIS: The Last Will and Testament payload
    offline_payload = json.dumps({
        "cpu_power": 0, "cpu_usage": 0, "cpu_temp": 0, 
        "gpu_power": 0, "gpu_usage": 0, "gpu_temp": 0,
        "nvme_temp": 0, "ram_temp": 0,
        "total_power": 0
    })
    mqtt_c.will_set(MQTT_TOPIC, offline_payload, retain=False)

    try:
        mqtt_c.connect(MQTT_HOST)
        print("Connected to MQTT broker.")
    except Exception as e:
        print(f"MQTT Error: {e}")

    def handle_exit(signum, frame):
        print("System shutdown detected. Pushing zeros to Home Assistant...")
        mqtt_c.publish(MQTT_TOPIC, offline_payload)
        mqtt_c.disconnect()
        sys.exit(0)

    # Intercept systemd shutdown (SIGTERM) and manual stops (SIGINT)
    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    # Start threads
    poller = threading.Thread(target=sensor_polling_thread, args=(mqtt_c,), daemon=True)
    poller.start()

    print("Entering monitoring loop...")
    animation_thread(mobo, cooler)

if __name__ == "__main__":
    main()