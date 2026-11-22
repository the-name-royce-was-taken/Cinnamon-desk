
import asyncio
import hashlib
import base64
import struct
import os
import mimetypes

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

PA = "/nix/store/pn9glkalcj7i5p549dpsl1c46pkb13xr-pulseaudio-17.0"
PACTL = PA + "/bin/pactl"
PAREC = PA + "/bin/parec"


async def send_ws_frame(writer, payload, opcode=2):
    size = len(payload)

    if size < 126:
        header = bytes([0x80 | opcode, size])
    elif size < 65536:
        header = bytes([0x80 | opcode, 126]) + struct.pack(">H", size)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack(">Q", size)

    writer.write(header + payload)
    await writer.drain()


async def find_audio_source(capture_env):
    """
    Find the virtual desktop audio monitor.
    Wait until Replit has created it.
    """

    while True:
        try:
            check = await asyncio.create_subprocess_exec(
                PACTL,
                "list",
                "short",
                "sources",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=capture_env,
            )

            output, _ = await check.communicate()

            if check.returncode == 0:
                for line in output.decode(errors="replace").splitlines():
                    parts = line.split()

                    if (
                        len(parts) >= 2
                        and parts[1] == "null.monitor"
                    ):
                        print("Found desktop audio source:", parts[1])
                        return parts[1]

        except Exception as exc:
            print("Audio source detection error:", repr(exc))

        print("Waiting for desktop_audio.monitor...")
        await asyncio.sleep(2)


async def handle_audio(reader, writer, key):
    """
    Stream only the PulseAudio virtual desktop output.
    Automatically find and retry the audio source.
    No microphone or physical input device is accessed.
    """

    capture_env = os.environ.copy()
    capture_env.pop("LD_LIBRARY_PATH", None)

    proc = None
    tasks = []

    accept = base64.b64encode(
        hashlib.sha1((key + GUID).encode()).digest()
    ).decode()

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )

    try:
        # Accept the WebSocket first, then wait for audio to become available.
        writer.write(response.encode())
        await writer.drain()

        async def audio_to_browser():
            nonlocal proc

            while not writer.is_closing():
                try:
                    source = await find_audio_source(capture_env)

                    print("Starting audio capture:", source)

                    proc = await asyncio.create_subprocess_exec(
                        PAREC,
                        "--device=" + source,
                        "--format=s16le",
                        "--rate=48000",
                        "--channels=2",
                        "--raw",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        env=capture_env,
                    )

                    while not writer.is_closing():
                        data = await proc.stdout.read(3840)

                        if not data:
                            break

                        await send_ws_frame(writer, data)

                    if proc.returncode is None:
                        proc.terminate()

                        try:
                            await asyncio.wait_for(
                                proc.wait(), timeout=2
                            )
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()

                    proc = None

                    if not writer.is_closing():
                        print("Audio capture stopped. Retrying...")
                        await asyncio.sleep(2)

                except asyncio.CancelledError:
                    raise

                except Exception as exc:
                    print("Audio capture error:", repr(exc))

                    if proc is not None:
                        if proc.returncode is None:
                            proc.kill()
                            await proc.wait()

                        proc = None

                    if not writer.is_closing():
                        await asyncio.sleep(2)

        async def browser_to_audio():
            while True:
                b0, b1 = await reader.readexactly(2)

                opcode = b0 & 0x0F
                masked = (b1 & 0x80) != 0
                length = b1 & 0x7F

                if length == 126:
                    length = int.from_bytes(
                        await reader.readexactly(2), "big"
                    )
                elif length == 127:
                    length = int.from_bytes(
                        await reader.readexactly(8), "big"
                    )

                mask = (
                    await reader.readexactly(4)
                    if masked else b"\x00\x00\x00\x00"
                )

                raw = await reader.readexactly(length)

                payload = (
                    bytes(
                        value ^ mask[i % 4]
                        for i, value in enumerate(raw)
                    )
                    if masked else raw
                )

                if opcode == 8:
                    return

                if opcode == 9:
                    await send_ws_frame(writer, payload, opcode=10)

        tasks = [
            asyncio.create_task(audio_to_browser()),
            asyncio.create_task(browser_to_audio()),
        ]

        await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED
        )

    except (ConnectionError, BrokenPipeError, asyncio.IncompleteReadError):
        pass

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print("Audio WebSocket ended:", repr(exc))

    finally:
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if proc is not None:
            if proc.returncode is None:
                proc.terminate()

                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

        print("Audio WebSocket closed")


async def handle_client(reader, writer):
    try:
        req = await reader.readuntil(b"\r\n\r\n")
        lines = req.decode().split("\r\n")

        path = lines[0].split(" ")[1].split("?")[0]

        headers = {
            k.lower(): v
            for line in lines[1:]
            if ": " in line
            for k, v in (line.split(": ", 1),)
        }

        key = headers.get("sec-websocket-key")

        # Normal HTTP requests serve the noVNC files.
        if not key:
            if path == "/":
                path = "/vnc.html"

            file_path = os.path.normpath("novnc" + path)

            if file_path.startswith("novnc") and os.path.isfile(file_path):
                with open(file_path, "rb") as f:
                    body = f.read()

                ctype = (
                    mimetypes.guess_type(file_path)[0]
                    or "application/octet-stream"
                )

                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    + f"Content-Type: {ctype}\r\n".encode()
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"\r\n"
                    + body
                )
            else:
                resp = (
                    b"HTTP/1.1 404 Not Found\r\n"
                    b"Content-Length: 9\r\n\r\n"
                    b"Not Found"
                )

            writer.write(resp)
            await writer.drain()
            return

        # Audio gets its own WebSocket route.
        if path == "/audio":
            await handle_audio(reader, writer, key)
            return

        # All other WebSocket connections are VNC.
        accept = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()
        ).decode()

        proto = (
            "Sec-WebSocket-Protocol: binary\r\n"
            if "binary" in headers.get("sec-websocket-protocol", "")
            else ""
        )

        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            f"{proto}\r\n"
        )

        writer.write(resp.encode())
        await writer.drain()

        vnc_r, vnc_w = await asyncio.open_connection(
            "127.0.0.1", 5900
        )

        async def ws_to_vnc():
            while True:
                b0, b1 = await reader.readexactly(2)

                opcode = b0 & 0x0F

                if opcode == 8:
                    break

                masked = (b1 & 0x80) != 0
                length = b1 & 0x7F

                if length == 126:
                    length = int.from_bytes(
                        await reader.readexactly(2), "big"
                    )
                elif length == 127:
                    length = int.from_bytes(
                        await reader.readexactly(8), "big"
                    )

                mask = (
                    await reader.readexactly(4)
                    if masked else b"\x00\x00\x00\x00"
                )

                raw = await reader.readexactly(length)

                payload = (
                    bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
                    if masked else raw
                )

                if opcode in (1, 2):
                    vnc_w.write(payload)
                    await vnc_w.drain()

        async def vnc_to_ws():
            import time

            last_report = time.monotonic()
            last_data = time.monotonic()
            total_bytes = 0
            chunks = 0
            max_gap = 0

            while data := await vnc_r.read(4096):
                now = time.monotonic()

                gap = now - last_data
                max_gap = max(max_gap, gap)

                last_data = now
                total_bytes += len(data)
                chunks += 1

                await send_ws_frame(writer, data)
                await asyncio.sleep(0)

                if now - last_report >= 2:
                    print(
                        f"VNC traffic: {total_bytes} bytes, "
                        f"{chunks} chunks in 2 seconds, "
                        f"largest gap: {max_gap * 1000:.1f} ms"
                    )

                    total_bytes = 0
                    chunks = 0
                    max_gap = 0
                    last_report = now

        await asyncio.gather(
            ws_to_vnc(),
            vnc_to_ws()
        )

    except Exception as exc:
        print("Client connection ended:", repr(exc))

    finally:
        writer.close()

        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(
        handle_client,
        "0.0.0.0",
        8080
    )

    print("noVNC and audio WebSocket server listening on 8080")

    async with server:
        await server.serve_forever()


asyncio.run(main())
