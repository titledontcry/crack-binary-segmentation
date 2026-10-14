# Jetson Nano camera agent

This replaces the old `~/vision` runtime for crack inspection. It does **not** run the crack model or issue MAVLink/Pixhawk control commands. It can listen read-only for forwarded MAVLink telemetry, then:

1. serves `http://JETSON_IP:8080/live` as MJPEG for the ground-station dashboard;
2. records a 1280×720 inspection image every configured interval;
3. stores it in a local spool first; and
4. adds fresh Pixhawk telemetry to the photo metadata; and
5. uploads it to the ground station, retrying until accepted.

## Install on the Jetson

From the Mac repository root, copy this directory to the Jetson (replace the IP if it changes):

```bash
scp -r drone_inference/jetson arnon@10.196.189.38:/home/arnon/crack-ground-station
```

On the Jetson:

```bash
cd ~/crack-ground-station
python3 -m pip install --user -r requirements.txt
ls -l /dev/cam_down /dev/cam_front
python3 crack_agent.py --endpoint http://GROUND_STATION_IP:8000 --camera /dev/cam_down --capture-interval 5 --mavlink-endpoint udpin:127.0.0.1:14551 --camera-offset-m 0.125
```

The listener above expects a MAVLink forwarder (for example MAVProxy) to send telemetry to UDP port `14551` on the Jetson. Keep the existing Pixhawk serial owner as-is; do not open its serial port a second time from this camera agent. Example MAVProxy forwarding option, added to the existing MAVProxy launch command:

```bash
--out=udp:127.0.0.1:14551
```

`--camera-offset-m 0.125` assumes the barometer/flight-controller reference is 12.5 cm above the camera; use a negative value if the camera is above that reference. The agent projects this offset using fresh roll/pitch when available. `height_m` remains an estimate relative to the autopilot's home/origin, not height above the local surface (AGL). It must not be treated as a ground-distance measurement on uneven terrain. The raw `relative_alt_m` is also included for auditability. Lens focal length and ISO are not fabricated; they remain absent unless the camera supplies them.

Check `http://JETSON_IP:8080/health`: `telemetry.connected` should become `true` after MAVLink messages arrive. If it stays false, check MAVProxy forwarding and `telemetry.error` there.

Use the Mac's LAN IP for `GROUND_STATION_IP`, not `localhost`. Confirm `http://JETSON_IP:8080/health` and `http://JETSON_IP:8080/live` first, then set this on the Mac's `drone_inference/config.yaml`:

```yaml
live_stream_url: "http://JETSON_IP:8080/live"
```

## Photo cadence

`--capture-interval 5` is intentionally conservative: images are captured every 5 seconds and then uploaded/inferred asynchronously. Benchmark the complete system before decreasing it. The spool retains originals after upload by default (`--keep-sent 0`).

## Start at boot

After the manual run works, copy `crack-agent.service` to `/etc/systemd/system/`, replace `GROUND_STATION_IP`, then enable it:

```bash
sudo cp crack-agent.service /etc/systemd/system/crack-agent.service
sudo sed -i 's/GROUND_STATION_IP/192.168.1.10/g' /etc/systemd/system/crack-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now crack-agent
sudo systemctl status crack-agent --no-pager
```

Do not run `vision/stream.py` at the same time: both programs would compete for the same V4L2 camera device.
