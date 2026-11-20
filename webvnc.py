import asyncio, hashlib, base64, struct, os, mimetypes

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


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


async def handle_audio(reader, writer, key):
    # Capture only the virtual desktop sink. No microphone is opened.
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "parec",
            "--device=desktop_audio.monitor",
            "--format=s16le",
            "--rate=48000",
            "--channels=2",
            "--raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        first_chunk = await asyncio.wait_for(
            proc.stdout.readexactly(3840), timeout=3.0
        )
    except Exception:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        body = b"Desktop audio is unavailable"
        writer.write(
            b"HTTP/1.1 503 Service Unavailable\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        return

    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    writer.write(response.encode())
    await writer.drain()

    async def audio_to_browser():
        chunk = first_chunk
        while True:
            await send_ws_frame(writer, chunk)
            chunk = await proc.stdout.readexactly(3840)

    async def browser_to_audio():
        while True:
            b0, b1 = await reader.readexactly(2)
            opcode = b0 & 0x0F
            masked = (b1 & 0x80) != 0
            length = b1 & 0x7F
            if length == 126:
                length = int.from_bytes(await reader.readexactly(2), "big")
            elif length == 127:
                length = int.from_bytes(await reader.readexactly(8), "big")
            mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
            raw = await reader.readexactly(length)
            payload = (
                bytes(value ^ mask[i % 4] for i, value in enumerate(raw))
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
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


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
        if not key:
            if path == "/":
                path = "/vnc.html"
            file_path = os.path.normpath("novnc" + path)
            if file_path.startswith("novnc") and os.path.isfile(file_path):
                with open(file_path, "rb") as f:
                    body = f.read()
                ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    + f"Content-Type: {ctype}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
            else:
                resp = b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
            writer.write(resp)
            await writer.drain()
            writer.close()
            return

        if path == "/audio":
            await handle_audio(reader, writer, key)
            return

        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        proto = "Sec-WebSocket-Protocol: binary\r\n" if "binary" in headers.get("sec-websocket-protocol", "") else ""
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            f"{proto}\r\n"
        )
        writer.write(resp.encode())
        await writer.drain()

        vnc_r, vnc_w = await asyncio.open_connection("127.0.0.1", 5900)

        async def ws_to_vnc():
            while True:
                b0, b1 = await reader.readexactly(2)
                opcode = b0 & 0x0F
                if opcode == 8:
                    break
                masked = (b1 & 0x80) != 0
                length = b1 & 0x7F
                if length == 126:
                    length = int.from_bytes(await reader.readexactly(2), "big")
                elif length == 127:
                    length = int.from_bytes(await reader.readexactly(8), "big")
                mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
                raw = await reader.readexactly(length)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(raw)) if masked else raw
                if opcode in (1, 2):
                    vnc_w.write(payload)
                    await vnc_w.drain()

        async def vnc_to_ws():
            while data := await vnc_r.read(4096):
                n = len(data)
                if n < 126:
                    frame = bytes([0x82, n]) + data
                elif n < 65536:
                    frame = bytes([0x82, 126]) + struct.pack(">H", n) + data
                else:
                    frame = bytes([0x82, 127]) + struct.pack(">Q", n) + data
                writer.write(frame)
                await writer.drain()

        await asyncio.gather(ws_to_vnc(), vnc_to_ws())
    except Exception:
        pass
    finally:
        writer.close()

async def main():
    server = await asyncio.start_server(handle_client, "0.0.0.0", 8080)
    print("noVNC client and WebSocket bridge listening on 8080")
    async with server:
        await server.serve_forever()

asyncio.run(main())
