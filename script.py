import network
import time
from usocket import socket
from machine import Pin, ADC
from pyb import DAC, USB_VCP

USE_DHCP = True

# Hardware
 #led pins
led_hot  = Pin('LED3', Pin.OUT_PP)
led_cold = Pin('LED2', Pin.OUT_PP)
led_hot.low(); led_cold.low()
sw = Pin('SW', Pin.IN)

#dac pins and values and adc pin
dac = DAC(2, bits=12)
dac_min, dac_max = 0, 4095
temp_min, temp_max = 15.0, 35.0
dac_value = int((22.0 - temp_min) / (temp_max - temp_min) * dac_max)
dac.write(dac_value)
adc = ADC(Pin("D24", Pin.ANALOG))

# Modes
mode_heating = "HEATING"
mode_cooling = "COOLING"
mode_idle    = "IDLE"
current_mode = mode_idle

# Control modes
control_auto   = "AUTO"
control_manual = "MANUAL"
control_mode   = control_auto

# parameters for main process
target_temp = 22.0  # target for processing automate mode
tolerance   = 0.3  # absolute value for accepting temperature as target
dac_step    = 15  # speed

hot_threshold  = 30.0  #alarm above this
cold_threshold = 18.0  #alarm below this
hysteresis     = 2.0   
state_normal   = "NORMAL"
state_too_hot  = "TOO HOT"
state_too_cold = "TOO COLD"
current_state  = state_normal
previous_state = state_normal

# SW
sw_prev   = 1 
sw_primed = False #flag for first default case of acknowledgement
ack_msg   = ""

# stores latest temperature values
history_size = 100
history = []

vcp = USB_VCP()


# HELPER FUNCTIONS
# convert raw value to temperature
def dac_to_temp(raw):
    return temp_min + (raw / dac_max) * (temp_max - temp_min)
# read value on adc pin then convert into temperature
def read_temperature():
    return dac_to_temp(adc.read_u16() >> 4)
# led pattern
def apply_leds(state):
    led_hot.high()  if state == state_too_hot  else led_hot.low()
    led_cold.high() if state == state_too_cold else led_cold.low()
# insert temperature to history to show on graph
def push_sample(temp):
    history.append(temp)
    if len(history) > history_size:
        del history[0]
# state determination based temperature
def determine_state(temp):
    global current_state
    if current_state == state_too_hot:
        if temp < hot_threshold - hysteresis:
            current_state = state_normal
    elif current_state == state_too_cold:
        if temp > cold_threshold + hysteresis:
            current_state = state_normal
    else:
        if temp > hot_threshold:
            current_state = state_too_hot
        elif temp < cold_threshold:
            current_state = state_too_cold
    return current_state
# send alarm from board
def send_alarm(state):
    if state == state_too_hot:   vcp.send("ALARM: HOT\r\n")
    elif state == state_too_cold: vcp.send("ALARM: COLD\r\n")
    else:                         vcp.send("OK: back to normal\r\n")


# -- MODE CONTROL --
# for manual mode, when user press mode buttons
def set_mode(new_mode):
    global current_mode, control_mode
    if new_mode in (mode_heating, mode_cooling, mode_idle):
        control_mode = control_manual
        current_mode = new_mode
        vcp.send("Mode -> %s (manual)\r\n" % current_mode)
        return True
    return False
# for automate mode, when new target temperature is set 
def auto_mode(temp):
    global current_mode
    if temp < target_temp - tolerance:
        new_mode = mode_heating
    elif temp > target_temp + tolerance:
        new_mode = mode_cooling
    else:
        new_mode = mode_idle
    if new_mode != current_mode:
        current_mode = new_mode
        vcp.send("Mode -> %s (auto)\r\n" % current_mode)
    return current_mode
# setting target temperature, it will also switch mode to auto
def set_target(value):
    global target_temp, control_mode
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    target_temp = max(temp_min, min(temp_max, v))
    control_mode = control_auto
    vcp.send("Target -> %.2f C (AUTO)\r\n" % target_temp)
    return True
# modifying temperature based on selected mode
def step_environment():
    global dac_value
    if current_mode == mode_heating:
        dac_value = min(dac_max, dac_value + dac_step)
        dac.write(dac_value)
    elif current_mode == mode_cooling:
        dac_value = max(dac_min, dac_value - dac_step)
        dac.write(dac_value)


# SW button activation for state acknowledgement 
def poll_sw_button():
    global sw_prev, ack_msg, sw_primed
    cur = sw.value()
    if not sw_primed:
        sw_prev = cur
        sw_primed = True
        return
    if cur == 0 and sw_prev == 1:
        ack_msg = "State acknowledged (local SW button)"
        vcp.send(ack_msg + "\r\n")
    sw_prev = cur


# vcp commands to verification from board
def handle_vcp_commands():
    global ack_msg
    if not vcp.any():
        return
    cmd = vcp.recv(1)
    if not cmd:
        return
    if cmd == b's':
        vcp.send("Temp=%.2f C | Target=%.2f C | Control=%s | Mode=%s | State=%s\r\n"
                 % (read_temperature(), target_temp, control_mode,
                    current_mode, current_state))
    elif cmd == b'a':
        ack_msg = "State acknowledged (VCP)"
        vcp.send(ack_msg + "\r\n")


# HTML page
def web_page(temp, state):
    global ack_msg, control_mode

    samples_csv = ",".join("%.2f" % v for v in history) if history else ""
    ack_line = ack_msg if ack_msg else "(no acknowledgement yet)"

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Room Temperature Controller</title>
  <script>
  var refreshEnabled = true;
  var refreshTimer = null;
  function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    if (refreshEnabled) {
      refreshTimer = setTimeout(function () {
        if (refreshEnabled) window.location.href = '/';
      }, 5000);
    }
  }
  function pauseRefresh() {
    refreshEnabled = false;
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
  }
  function resumeRefresh() {
    refreshEnabled = true;
    scheduleRefresh();
  }
  scheduleRefresh();
  </script>
  <style>
    body { font-family: Arial, sans-serif; background:#1e1e2f; color:#eee;
           margin:0; padding:20px; }
    h1   { color:#4fc3f7; text-align:center; }
    .card { background:#2a2a40; border-radius:10px; padding:18px 24px;
            margin:0 0 14px 0; box-shadow:0 2px 8px #0006; }
    .big { font-size:2.2em; color:#ffd54f; font-weight:bold; }
    .target { font-size:1.4em; color:#81c784; font-weight:bold; }
    .st-normal { color:#66bb6a; }
    .st-hot    { color:#e53935; }
    .st-cold   { color:#29b6f6; }
    .mode-heat { color:#e53935; }
    .mode-cool { color:#29b6f6; }
    .mode-idle { color:#b0bec5; }
    input[type=number] { width:100px; padding:6px; border-radius:5px;
                         border:none; }
    .apply { background:#43a047; border:none; cursor:pointer;
             padding:8px 18px; border-radius:5px; color:#fff;
             font-weight:bold; }
    .btn { display:inline-block; padding:10px 22px; margin:6px;
           border-radius:6px; text-decoration:none; color:#fff;
           font-weight:bold; font-size:1.05em; }
    .heat { background:#e53935; }
    .cool { background:#29b6f6; }
    .idle { background:#757575; }
    .ack  { color:#ffd54f; font-style:italic; }
    svg  { display:block; width:100%; height:auto; }
    .grid { stroke:#333; stroke-width:0.5; }
    .lbl  { fill:#aaa; font-size:11px; }
    .layout { display: grid; grid-template-columns: 1fr 1fr;
              gap:14px; max-width:1200px; margin:0 auto; }
    .col { min-width:0; }
    @media (max-width: 800px) { .layout { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <h1>Room Temperature Controller</h1>

  <div class="layout">

    <!-- LEFT -->
    <div class="col">
      <div class="card" style="text-align:center;">
        Current temperature<br>
        <span class="big">""" + "%.1f" % temp + """ &deg;C</span>
      </div>

      <div class="card" style="text-align:center;">
        Target temperature<br>
        <span class="target">""" + "%.1f" % target_temp + """ &deg;C</span>
      </div>

      <div class="card">
        Control: <b>""" + control_mode + """</b>
        <span style="color:#888;font-size:0.85em;">(from last action)</span><br>
        Mode: <b class=\"""" + (
            "mode-heat" if current_mode == mode_heating else
            "mode-cool" if current_mode == mode_cooling else
            "mode-idle"
        ) + """\">""" + current_mode + """</b><br>
        Alarm: <b class=\"""" + (
            "st-normal" if state == state_normal else
            "st-hot"    if state == state_too_hot else
            "st-cold"
        ) + """\">""" + state + """</b>
      </div>

      <div class="card">
        <h2 style="margin-top:0;">Set target temperature (AUTO)</h2>
        <form action="/" method="get">
          <input type="number" name="target" min="15" max="35" step="0.5"
                 value=\"""" + "%.1f" % target_temp + """\"
                 onfocus="pauseRefresh()"
                 onblur="resumeRefresh()">
          <button class="apply" type="submit">Apply</button>
        </form>
      </div>

      <div class="card" style="text-align:center;">
        <h2 style="margin-top:0;">Manual mode control</h2>
        <a class="btn heat" href="/?action=heat">Heating</a>
        <a class="btn cool" href="/?action=cool">Cooling</a>
        <a class="btn idle" href="/?action=idle">Idle</a>
      </div>
    </div>

    <!-- RIGHT -->
    <div class="col">
      <div class="card">
        <h2 style="margin-top:0;">Temperature history</h2>
        <svg id="graph" viewBox="0 0 600 200"
             preserveAspectRatio="none" aria-label="temperature history"></svg>
        <div class="lbl" style="text-align:right;">
          last """ + str(len(history)) + """ samples &mdash; fixed Y axis 15&ndash;35 &deg;C
        </div>
      </div>
      <div class="card">
        <h2 style="margin-top:0;">Acknowledgement message</h2>
        <span class="ack">""" + ack_line + """</span>
      </div>
    </div>

  </div>

  <p style="text-align:center;color:#888;margin-top:20px;">
    Auto-refresh &mdash; Bayardorj Dagdandorj
  </p>

<script>
(function () {
  var csv = \"""" + samples_csv + """\";
  var TEMP_MIN = 15.0, TEMP_MAX = 35.0;
  var HOT = 30.0, COLD = 18.0;
  var TARGET = """ + "%.2f" % target_temp + """;
  var W = 600, H = 200, PAD = 20;
  var svg = document.getElementById('graph');

  function el(name, attrs) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  svg.appendChild(el('rect', {x:0, y:0, width:W, height:H, fill:'#22223a'}));

  for (var t = TEMP_MIN; t <= TEMP_MAX; t += 5) {
    var y = PAD + (1 - (t - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * (H - 2*PAD);
    svg.appendChild(el('line', {x1:PAD, x2:W-PAD, y1:y, y2:y, 'class':'grid'}));
    var lbl = el('text', {x:2, y:y+4, 'class':'lbl'});
    lbl.textContent = t.toFixed(0) + ' C';
    svg.appendChild(lbl);
  }

  [[HOT, '#e53935'], [COLD, '#29b6f6']].forEach(function (p) {
    var y = PAD + (1 - (p[0] - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * (H - 2*PAD);
    svg.appendChild(el('line', {
      x1:PAD, x2:W-PAD, y1:y, y2:y,
      stroke:p[1], 'stroke-width':1, 'stroke-dasharray':'4,4'
    }));
  });

  var ty = PAD + (1 - (TARGET - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * (H - 2*PAD);
  svg.appendChild(el('line', {
    x1:PAD, x2:W-PAD, y1:ty, y2:ty,
    stroke:'#81c784', 'stroke-width':2
  }));

  if (!csv) return;
  var vals = csv.split(',').filter(function (s) { return s.length > 0; })
                         .map(parseFloat);
  if (vals.length < 2) return;
  var n = vals.length, pts = [];
  for (var i = 0; i < n; i++) {
    var x = PAD + (i / (n - 1)) * (W - 2*PAD);
    var tv = Math.max(TEMP_MIN, Math.min(TEMP_MAX, vals[i]));
    var yv = PAD + (1 - (tv - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * (H - 2*PAD);
    pts.push(x.toFixed(1) + ',' + yv.toFixed(1));
  }
  svg.appendChild(el('polyline', {
    points: pts.join(' '),
    fill: 'none',
    stroke: '#ffd54f',
    'stroke-width': 2,
    'stroke-linejoin': 'round',
    'stroke-linecap': 'round'
  }));

  var last = Math.max(TEMP_MIN, Math.min(TEMP_MAX, vals[n - 1]));
  var lx = W - PAD;
  var ly = PAD + (1 - (last - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * (H - 2*PAD);
  svg.appendChild(el('circle', {cx:lx, cy:ly, r:4, fill:'#ffd54f'}));
})();
</script>
</body>
</html>"""
    return html


# Main function
def main():
    global previous_state, ack_msg

    ack_msg = ""
    previous_state = state_normal
    
    #Activating network interface
    nic = network.LAN(); nic.active(True)
    
    #DHCP and state configuration
    if USE_DHCP:
        nic.ifconfig('dhcp')   #interface request IP from DHCP server
        
        #bounded wait
        t = 10
        while not nic.isconnected() and t > 0:
            time.sleep(1); t -= 1
        if not nic.isconnected():
            print("[ERROR] DHCP failed"); return
    else:
        nic.ifconfig(('192.168.88.110', '255.255.255.0',
                      '192.168.88.1', '8.8.8.8'))      #static branch
        time.sleep_ms(500)
    print("[STATUS] http://%s/" % nic.ifconfig()[0])

# Creating and preparing socket
    s = socket()
    s.bind(('0.0.0.0', 80)) # it listen on all interfaces
    s.listen(5)
    s.settimeout(0.5)
    print("[STATUS] listening on :80")

    debug_printed = False

    while True:
        # Read temperature first
        temp = read_temperature()

       #incase of automate mode activation
        if control_mode == control_auto:
            auto_mode(temp)
        
        # Move the environment according to selected mode
        step_environment()
        # Re-read after the step
        temp = read_temperature()
        state = determine_state(temp)
        apply_leds(state)
        push_sample(temp)
        
        # update alarm state
        if state != previous_state:
            send_alarm(state)
            previous_state = state
            ack_msg = ""

        poll_sw_button()
        handle_vcp_commands()
        
        #HTTP
        try:
            conn, addr = s.accept()
        except OSError as e:
            if not debug_printed:
                print("[DEBUG] accept args:", e.args)
                debug_printed = True
            if e.args and abs(e.args[0]) == 110:
                continue
            print("Socket error:", e)
            continue
        # read up to 1024 bytes from connected client
        request = str(conn.recv(1024))
        print("Request from", addr)

    # Target dispatch
        # find target position from the request
        t_pos = request.find('target=')
        # target stay as it was, in case of no target change
        if t_pos != -1:
            end = request.find('&', t_pos)
            if end == -1:
                end = request.find(' ', t_pos)
            set_target(request[t_pos + 7:end])

        # Manual mode dispatch
        if   "action=heat" in request: set_mode(mode_heating)
        elif "action=cool" in request: set_mode(mode_cooling)
        elif "action=idle" in request: set_mode(mode_idle)
       
        # reading the fresh state for the response
        temp = read_temperature()
        state = determine_state(temp)
        response = web_page(temp, state)
        # sending HTTP response
        conn.send('HTTP/1.1 200 OK\r\n')
        conn.send('Connection: close\r\n')
        conn.send('Content-Type: text/html\r\n')
        conn.send('Content-Length: %s\r\n\r\n' % len(response))

        sent = 0
        while sent < len(response):
            sent += conn.send(response[sent:])

        conn.close()  # close the connection


if __name__ == "__main__":
    main()