import hid
import time
import subprocess
import psutil

VENDOR_ID = 13875
PRODUCT_ID = 5

def get_gpu_usage():
    try:
        # Your SSH hack to the Mint VM
        cmd = "ssh -o BatchMode=yes -o ConnectTimeout=2 ojobinv@192.168.1.237 'nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits'"
        out = subprocess.check_output(cmd, shell=True, text=True).strip()
        return int(out)
    except Exception:
        # Failsafe if the VM is off
        return 0

def get_cpu_usage():
    return round(psutil.cpu_percent())

def get_bar_value(val):
    # Calculates the 1-10 green LED segments
    if val <= 0: return 0
    return (val - 1) // 10 + 1

def build_dual_packet(cpu_val, gpu_val):
    data = [0] * 64
    data[0] = 16  # D0: REPORT ID
    
    # --- CPU SECTION (D1 to D5) ---
    data[1] = 76  # D1: CPU USAGE MODE
    data[2] = get_bar_value(cpu_val) # D2: CPU STATUS BAR
    
    cpu_chars = [int(c) for c in str(cpu_val)]
    if len(cpu_chars) == 1:
        data[5] = cpu_chars[0]
    elif len(cpu_chars) == 2:
        data[4] = cpu_chars[0]
        data[5] = cpu_chars[1]
    elif len(cpu_chars) == 3:
        data[3] = cpu_chars[0]
        data[4] = cpu_chars[1]
        data[5] = cpu_chars[2]
        
    # --- GPU SECTION (D6 to D10) ---
    data[6] = 76  # D6: GPU USAGE MODE
    data[7] = get_bar_value(gpu_val) # D7: GPU STATUS BAR
    
    gpu_chars = [int(c) for c in str(gpu_val)]
    if len(gpu_chars) == 1:
        data[10] = gpu_chars[0]
    elif len(gpu_chars) == 2:
        data[9] = gpu_chars[0]
        data[10] = gpu_chars[1]
    elif len(gpu_chars) == 3:
        data[8] = gpu_chars[0]
        data[9] = gpu_chars[1]
        data[10] = gpu_chars[2]
        
    return data

try:
    print("Connecting to DeepCool CH560...")
    device = hid.device()
    device.open(VENDOR_ID, PRODUCT_ID)
    device.set_nonblocking(1)
    
    # Wake up the display (D1 = 170)
    wake_packet = [0] * 64
    wake_packet[0] = 16
    wake_packet[1] = 170
    device.write(wake_packet)
    time.sleep(1)

    print("Streaming dual usage stats. Press Ctrl+C to stop.")
    
    # Infinite loop to update the screen every 2 seconds
    while True:
        current_cpu = get_cpu_usage()
        current_gpu = get_gpu_usage()
        
        # Build the exact packet from the Mapping Table
        packet = build_dual_packet(current_cpu, current_gpu)
        
        # Send it to the screen
        device.write(packet)
        time.sleep(2)
        
except KeyboardInterrupt:
    print("\nScript stopped by user.")
except Exception as e:
    print(f"Error: {e}")
finally:
    if 'device' in locals():
        device.close()